"""What the AI found in each picture / video, remembered: what it shows (CLIP vector), faces, when it was taken
and where (GPS -> town). Kept in %DATA%/ai/index.sqlite - in private mode only in memory (gone when the app
closes). A file that changed (size / time) is looked at again.

`INDEXER.request(paths, private)` looks at files in the background (the wall's folder first); `lookup(path,
private)` gives a Record once it's done. `INDEXER.progress` = (done, asked) for the wall to show.
"""
import json
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from ..core.log import log_error
from ..core.paths import DATA_DIR
from . import models

DISK_FILE = DATA_DIR / "ai" / "index.sqlite"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
LOOK_SIDE = 1280                  # pictures are read this big for faces (CLIP needs much less)


@dataclass
class Record:
    path: str
    vector: np.ndarray = None     # CLIP (512,) unit vector
    date: float = 0.0             # when taken (seconds since 1970): the photo's own date, else the file's
    place: str = ""               # "Lisbon, Portugal" (from GPS), "" if unknown
    gps: tuple = None             # (latitude, longitude) or None
    faces: list = field(default_factory=list)   # [(box (x, y, w, h) as parts, feature (128,))]


def _stamp(path):
    s = os.stat(path)
    return f"{s.st_size}|{s.st_mtime}"


class _Store:
    def __init__(self, file):
        if file is not None:
            Path(file).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(file) if file else ":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS media (path TEXT PRIMARY KEY, stamp TEXT, vector BLOB, "
                             "date REAL, place TEXT, faces TEXT, lat REAL, lon REAL)")
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(media)")}
            for column in ("lat", "lon"):          # an index made before GPS was kept
                if column not in columns:
                    self._db.execute(f"ALTER TABLE media ADD COLUMN {column} REAL")
            self._db.commit()
        self._cache = {}

    def get(self, path):
        if path in self._cache:
            return self._cache[path]
        with self._lock:
            row = self._db.execute("SELECT stamp, vector, date, place, faces, lat, lon FROM media WHERE path = ?",
                                   (path,)).fetchone()
        if row is None:
            return None
        try:
            if row[0] != _stamp(path):
                return None
        except OSError:
            return None
        faces = [(tuple(box), np.array(feature, np.float32)) for box, feature in json.loads(row[4] or "[]")]
        record = Record(path, vector=np.frombuffer(row[1], np.float16).astype(np.float32) if row[1] else None,
                        date=row[2] or 0.0, place=row[3] or "", faces=faces,
                        gps=(row[5], row[6]) if row[5] is not None else None)
        self._cache[path] = record
        return record

    def put(self, record):
        faces = json.dumps([(list(box), [round(float(x), 4) for x in feature]) for box, feature in record.faces])
        vector = record.vector.astype(np.float16).tobytes() if record.vector is not None else None
        with self._lock:
            lat, lon = record.gps if record.gps else (None, None)
            self._db.execute("INSERT OR REPLACE INTO media VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (record.path, _stamp(record.path), vector, record.date, record.place, faces, lat, lon))
            self._db.commit()
        self._cache[record.path] = record


_stores = {}


def store(private):
    key = "memory" if private else "disk"
    if key not in _stores:
        _stores[key] = _Store(None if private else DISK_FILE)
    return _stores[key]


def lookup(path, private):
    return store(private).get(str(path)) or (store(False).get(str(path)) if private else None)


# -- reading a file -------------------------------------------------------------------------------------------
def _exif_date_and_gps(image):
    date, gps = None, None
    try:
        exif = image.getexif()
        sub = exif.get_ifd(0x8769)
        text = sub.get(36867) or sub.get(36868) or exif.get(306)
        if text:
            date = datetime.strptime(str(text).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        g = exif.get_ifd(0x8825)
        if g and 2 in g and 4 in g:
            def degrees(v):
                return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            lat, lon = degrees(g[2]), degrees(g[4])
            if g.get(1) == "S":
                lat = -lat
            if g.get(3) == "W":
                lon = -lon
            if lat or lon:
                gps = (lat, lon)
    except Exception:
        pass
    return date, gps


def _read(path):
    """(PIL picture to look at, date or None, gps or None)."""
    from PIL import Image
    from ..video import thumbs
    if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
        image, _ = thumbs._video_frame(Path(path))
        return thumbs._one_eye(image.convert("RGB"), Path(path)), None, None
    with Image.open(path) as image:
        date, gps = _exif_date_and_gps(image)
        image.draft("RGB", (LOOK_SIDE, LOOK_SIDE))
        image = image.convert("RGB")
    image.thumbnail((LOOK_SIDE, LOOK_SIDE))
    return thumbs._one_eye(image, Path(path)), date, gps


class Indexer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="ai-indexer")
        self._queue = queue.Queue()
        self._asked = set()
        self.progress = (0, 0)
        self.state = ""                     # "", "downloading 40%", "failed"
        self.start()

    def request(self, paths, private):
        """Look at these files (those not known yet), in this order."""
        new = [str(p) for p in paths if (str(p), private) not in self._asked and lookup(p, private) is None]
        if not new:
            return
        for p in new:
            self._asked.add((p, private))
        done, asked = self.progress
        self.progress = (done, asked + len(new))
        self._queue.put((new, private))

    def busy(self):
        done, asked = self.progress
        return done < asked

    def run(self):
        while True:
            paths, private = self._queue.get()
            for group in ("clip", "faces", "places"):
                if not models.ready(group):
                    self.state = "downloading AI models"
                    models.ensure(group)
            self.state = "" if all(models.ready(g) for g in ("clip", "faces", "places")) else "failed"
            if self.state:
                continue
            from . import clip, faces, places
            for i in range(0, len(paths), clip.BATCH):
                batch = paths[i:i + clip.BATCH]
                read = []
                for p in batch:
                    try:
                        read.append((p, *_read(p)))
                    except Exception:
                        log_error(f"AI look at {Path(p).name}")
                        self._asked.discard((p, private))
                try:
                    vectors = clip.images([r[1] for r in read])
                except Exception:
                    log_error("AI clip")
                    vectors = [None] * len(read)
                for (p, image, date, gps), vector in zip(read, vectors):
                    try:
                        found = faces.find(np.asarray(image))
                    except Exception:
                        log_error(f"AI faces {Path(p).name}")
                        found = []
                    try:
                        when = date or os.path.getmtime(p)
                        place = places.name_of(*gps) if gps else ""
                        store(private).put(Record(p, vector=vector, date=when, place=place or "", gps=gps,
                                                  faces=found))
                    except Exception:
                        log_error(f"AI index {Path(p).name}")
                done, asked = self.progress
                self.progress = (done + len(batch), asked)
            time.sleep(0)


INDEXER = None


def indexer():
    global INDEXER
    if INDEXER is None:
        INDEXER = Indexer()
    return INDEXER
