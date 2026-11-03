"""The photo wall: a folder's pictures and videos around you (Photos > Photo wall).

Shapes: a sphere (rows follow a ball around you), a ring (a round tower of rows), a flat wall - and the memory
galaxy: every picture a star in the space around you, similar ones together (what they show, seen by the AI),
groups named ("beach", "night", "food"...). You fly through it with the wheel.
Moving: grab anywhere (a picture too) and drag - a flat wall slides, a ring turns and its rows go up / down, a
sphere and the galaxy turn around you. A quick drag glides on. Wheel: zoom toward where you point (galaxy:
fly), Alt + wheel: distance (galaxy: picture size). Whenever what shows changes, the pictures fly to their new
places.
Clicking a picture opens it big (Previous / Next go through the wall), a video plays around you; Exit comes back.
Pointing at a video plays it on the wall (sound: 🔇 / 🔊 on the bar, muted to start); "Live wall" plays several
at once, slowly - the one you point / look at at normal speed (with sound if on). "Look to pick": looking at a
picture for a moment opens it (off unless turned on).
The AI (ai/, all on this PC) looks at the folder in the background: search / questions (spoken: press Talk)
filter the wall and may answer ("Last time in Lisbon: 2 June 2025"); People shows the faces found
- one click and every picture of that person flies to you. Memory rooms keep a wall as it is (folder, search,
shape, settings, surroundings) to open again later.
"""
import collections
import math
import os
import queue
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..ai import ask as ai_ask
from ..ai import index as ai_index
from ..core import geometry
from ..core.log import log_error
from ..core.settings import LIMITS, Settings, clamp
from ..render.gl import Texture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Card, Column, Label, Panel, Row, Slider, Spacer, Toggle
from ..video import files
from ..video.thumbs import THUMBNAILS
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT

MAX_ITEMS = 5000                   # read from a folder at most
ALL_AT_ONCE = 400                  # "All" on one page: at most this many (the thumbnails' memory keeps 400)
PER_PAGE = ((24, "24"), (48, "48"), (96, "96"), (200, "200"), (0, "All"))
UPLOADS_PER_FRAME = 6
GROW_SPEED = 12.0                  # how fast a pointed-at picture grows / shrinks back (1/s)
HOVER_FRAME = (0.31, 0.61, 0.98, 1.0)
LOOK_FRAME = (0.98, 0.78, 0.31, 1.0)
EMPTY_COLOR = (0.16, 0.18, 0.23, 1.0)   # a picture whose thumbnail isn't made yet
FULL_CIRCLE = 0.95                 # a row may go this far around you (ring / sphere); longer: more rows
MAX_ROWS = 40
DRAG_DEGREES = 1.0                 # a press that moves less than this is a click
GLIDE_SLOWDOWN = 4.0               # after a quick drag the wall glides on, slowing down by this (1/s)
GLIDE_MAX = 12.0                   # ...at most this fast (picture heights / s)
WHEEL_ZOOM = 1.12                  # per wheel notch
FLY_STEP = 0.45                    # galaxy: m per wheel notch (in a galaxy of GALAXY_RADIUS: grows with its space)
FLY_SECONDS = 0.8                  # pictures flying to new places
SHARP_ANGLE = math.radians(22)     # a picture looking taller than this loads its full picture...
SHARP_SIZE = 2048                  # ...this big
SHARP_KEEP = 12                    # full pictures kept on the GPU
PREVIEW_WIDTH = 640                # a playing video on the wall is this wide
LIVE_WIDTH = 400                   # ...in the live wall (several at once)
PREVIEW_DELAY = 0.35               # s pointed at before it starts
PREVIEW_SOUND_CHUNKS = 64          # decoded sound waiting to be played
LIVE_MAX = 6                       # live wall: videos playing at once (the ones nearest where you look)
LIVE_SLOW = 0.4                    # ...at this speed, except the one you point / look at
AUDIO_RATE = 48000
HIT_MARGIN = math.radians(4)       # pictures farther than this from the pointer aren't hit-tested
LOOK_PICK_SECONDS = 1.5            # look to pick: looking this long opens it
GALAXY_RADIUS = 5.0                # m: most of the galaxy lies within this (the layout; drawn at wall_space)
GALAXY_NEAREST = 1.4               # m: no star nearer than this to where you start
GALAXY_GROW_FROM = 2.2             # m: stars farther than this are drawn bigger (up to 2.5x)
ANSWER_SECONDS = 12.0
CONTROLS_WIDTH = 1760
PANEL_WIDTH = 1120
SIDE_WIDTH = 560
ABOUT_WIDTH = 380                  # the folder name / count on the bar
PANEL_GAP = 0.02
SHAPES = (("sphere", "Sphere"), ("ring", "Ring"), ("flat", "Flat"), ("tower", "Tower"), ("column", "Column"),
          ("galaxy", "Galaxy"))
SHAPE_ROWS = {"sphere": 5, "ring": 4, "flat": 4, "column": 6}   # rows (column: pictures across) to start with
VERTICAL = ("tower", "column")     # rows going down that you move up / down through (tower: all around you)
FLAT_SHAPES = ("flat", "column")
AROUND_SHAPES = ("ring", "tower")  # cylinders: turn around you, move up / down
ORDERS = (("name", "Name"), ("newest", "Newest"), ("shuffle", "Shuffle"))
SHOWS = (("all", "All"), ("photos", "Photos"), ("videos", "Videos"))
WALL_DEFAULTS = {f.name: f.default for f in Settings.__dataclass_fields__.values() if f.name.startswith("wall_")}
ROOM_KEYS = [k for k in WALL_DEFAULTS]


def _time(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def _like_thumbnail(pixels, aspect):
    """The same part as the thumbnail shows (video/thumbs.py keeps one eye of a 3D picture, else all of it):
    `aspect` = the thumbnail's height / width."""
    h, w = pixels.shape[:2]
    if aspect and abs(h / (w / 2) - aspect) < abs(h / w - aspect) * 0.5:       # one eye of side by side
        return pixels[:, :w // 2]
    if aspect and abs((h / 2) / w - aspect) < abs(h / w - aspect) * 0.5:       # one eye of top / bottom
        return pixels[:h // 2]
    return pixels


def _facing(points):
    """(n, 4, 4) models of pictures at `points` (n, 3), each facing you (at the origin)."""
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    dist = np.maximum(np.linalg.norm(points, axis=1), 1e-6)
    yaw = np.arctan2(x, -z)                       # + = right
    pitch = np.arcsin(np.clip(y / dist, -1, 1))   # + = up
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    m = np.zeros((len(points), 4, 4), np.float32)
    # rotate_y(-yaw) @ rotate_x(pitch): the picture's normal (+z) points back at you
    m[:, 0, 0], m[:, 0, 1], m[:, 0, 2] = cy, -sy * sp, -sy * cp
    m[:, 1, 1], m[:, 1, 2] = cp, -sp
    m[:, 2, 0], m[:, 2, 1], m[:, 2, 2] = sy, cy * sp, cy * cp
    m[:, :3, 3] = points
    m[:, 3, 3] = 1.0
    return m


def _round_face(image, box):
    """A face cut out of a picture, round, 192 px."""
    w, h = image.size
    x, y, bw, bh = box
    cx, cy, half = (x + bw / 2) * w, (y + bh / 2) * h, max(bw * w, bh * h) * 0.8
    face = image.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))).convert("RGBA")
    face = face.resize((192, 192), Image.LANCZOS)
    mask = Image.new("L", (192, 192), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 191, 191), fill=255)
    face.putalpha(mask)
    return face


@dataclass(eq=False)
class _PictureSurface(Surface):
    """A picture's surface; only the ones near the pointer are hit-tested (hundreds would cost a lot)."""
    near: bool = True

    def hit(self, origin, direction):
        return super().hit(origin, direction) if self.near else None


class _Item:
    """One picture or video on the wall (also the target of its surface)."""

    def __init__(self, wall, path):
        self.wall, self.path = wall, str(path)
        self.video = Path(path).suffix.lower() in files.EXTENSIONS
        self.texture = None
        self.aspect = 0.75             # height / width, until the thumbnail tells
        self.grow = 1.0                # now (animated toward the zoom while pointed at)
        self.pose = None               # where it was last frame (4x4)
        self.fly_from, self.fly_start = None, 0.0
        try:
            self.time = os.path.getmtime(path)
        except OSError:
            self.time = 0.0

    def on_pointer(self, e):
        wall = self.wall
        if e.kind == "move":
            wall.point_at(self)
        elif e.kind == "leave":
            wall.point_at(None, self)
        elif e.kind == "wheel":
            wall.wheel(e)
        else:
            wall.grab.on_pointer(e, self)


class _Preview(threading.Thread):
    """A video playing on the wall, from a bit into it, over and over: `frame` is the newest picture (RGBA, the
    same part as its thumbnail, a time bar along the bottom), `seq` grows with each. `rate()`: its speed (the
    live wall plays others slowly); `volume()`: its sound (0 = muted); sound only plays at full speed."""

    def __init__(self, path, aspect, volume, rate=lambda: 1.0, width=PREVIEW_WIDTH):
        super().__init__(daemon=True, name="wall-preview")
        self.path, self.aspect, self.volume, self.rate, self.width = path, aspect, volume, rate, width
        self.frame, self.seq = None, 0
        self._stop = threading.Event()
        self._sound = queue.Queue(maxsize=PREVIEW_SOUND_CHUNKS)

    def stop(self):
        self._stop.set()

    def run(self):
        import av
        try:
            with av.open(self.path) as container:
                video = container.streams.video[0]
                video.thread_type = "AUTO"
                audio = container.streams.audio[0] if container.streams.audio else None
                if audio is not None:
                    threading.Thread(target=self._play_sound, daemon=True, name="wall-preview-sound").start()
                    resampler = av.AudioResampler(format="flt", layout="stereo", rate=AUDIO_RATE)
                duration = float(container.duration or 0) / 1_000_000
                start = min(duration * 0.1, 60) if duration > 10 else 0.0
                while not self._stop.is_set():
                    if start:
                        container.seek(int(start * 1_000_000))
                    first, played, last, shown = None, 0.0, time.monotonic(), self.seq
                    for packet in container.demux(*(s for s in (video, audio) if s is not None)):
                        for frame in packet.decode():
                            if self._stop.is_set():
                                return
                            t = frame.time or 0.0
                            if t < start - 0.05:      # the seek lands on the key frame before
                                continue
                            if packet.stream is audio:
                                if self.rate() >= 0.99:
                                    for out in resampler.resample(frame):
                                        try:
                                            self._sound.put_nowait(out.to_ndarray().reshape(-1, 2))
                                        except queue.Full:
                                            pass
                                continue
                            if first is None:
                                first = t
                            h = max(2, round(self.width * frame.height / frame.width / 2) * 2)
                            pixels = np.array(_like_thumbnail(
                                frame.to_ndarray(format="rgba", width=self.width, height=h), self.aspect))
                            if duration:
                                _time_bar(pixels, t / duration)
                            while played < t - first:         # wait for its time, at its speed
                                if self._stop.wait(0.01):
                                    return
                                now = time.monotonic()
                                played += (now - last) * self.rate()
                                last = now
                            self.frame, self.seq = pixels, self.seq + 1
                    if self.seq == shown:     # nothing played (start = 0 and a broken file): give up
                        return
        except Exception:
            log_error(f"wall preview {Path(self.path).name}")

    def _play_sound(self):
        """The sound card plays what the decoder put in the queue; the volume fades to its new level."""
        try:
            import sounddevice
            gain = 0.0
            with sounddevice.OutputStream(samplerate=AUDIO_RATE, channels=2, dtype="float32") as out:
                while not self._stop.is_set():
                    try:
                        samples = self._sound.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    target = self.volume()
                    ramp = np.linspace(gain, target, len(samples), dtype=np.float32)[:, None]
                    gain = target if abs(target - gain) < 0.02 else gain + (target - gain) * 0.3
                    out.write((samples * ramp).astype(np.float32))
        except Exception:
            log_error("wall preview sound")


def _time_bar(pixels, fraction):
    """Where the video is: a thin bar along the bottom of its picture (RGBA array, drawn in place)."""
    h, w = pixels.shape[:2]
    bar = max(4, h // 40)
    pixels[h - bar:, :, :3] = (pixels[h - bar:, :, :3] * 0.35).astype(np.uint8)
    pixels[h - bar:, :int(w * max(0.0, min(1.0, fraction))), :3] = theme.ACCENT[:3]


class _Sharp(threading.Thread):
    """Full pictures, read in the background: ask `want(path, aspect)`, collect `ready` = {path: RGBA array}."""

    def __init__(self):
        super().__init__(daemon=True, name="wall-sharp")
        self.ready = {}
        self._asked = set()
        self._queue = queue.LifoQueue()
        self.start()

    def want(self, path, aspect):
        if path not in self._asked:
            self._asked.add(path)
            self._queue.put((path, aspect))

    def forget(self, path):
        self._asked.discard(path)

    def run(self):
        while True:
            job = self._queue.get()
            if job is None:
                return
            path, aspect = job
            try:
                with Image.open(path) as image:
                    image.draft("RGB", (SHARP_SIZE * 2, SHARP_SIZE * 2))
                    image = image.convert("RGBA")
                pixels = _like_thumbnail(np.asarray(image), aspect)
                picture = Image.fromarray(np.ascontiguousarray(pixels))
                picture.thumbnail((SHARP_SIZE, SHARP_SIZE))
                self.ready[path] = np.asarray(picture)
            except Exception:
                log_error(f"wall picture {Path(path).name}")

    def stop(self):
        self._queue.put(None)


class PhotoWall:
    def __init__(self, shell, folder, room=None):
        self.shell, self.folder = shell, Path(folder)
        s = shell.settings()
        if s.wall_shape in SHAPE_ROWS:   # this shape's own rows
            shell.store.update(wall_rows=s.wall_shape_rows.get(s.wall_shape, SHAPE_ROWS[s.wall_shape]))
        self.title = self.folder.name or str(self.folder)
        self.all_items, self.more, self.loading = [], 0, True
        self.page = 0                            # which page of the wall's pictures shows
        self.front_yaw = shell.look_yaw          # the wall's middle is where you looked when it opened
        self.pan = [0.0, 0.0]                    # how far the pictures moved, in picture heights (right, up)
        self.velocity = (0.0, 0.0)               # gliding after a quick drag (picture heights / s)
        self.fly = np.zeros(3, np.float32)       # galaxy: where you flew to (m, in the galaxy)
        self.galaxy_turn = [0.0, 0.0]            # galaxy: turned around you (degrees: left / right, up / down)
        self.hovered = None
        self._hovered_since = 0.0
        self.looked, self._looked_since, self._look_rest = None, 0.0, 0.0
        self.settings_open = self.people_open = False
        self._seed = random.random()
        self._ordered = None                     # what the items are sorted for
        self.items = []                          # what Show / the search lets through, in order (all pages)
        self.shown = []                          # this page's part of them
        self._paged = None
        self._layout_key, self._layout = None, None
        self._last_frame = None
        self._last_key = None                    # the layout last frame (a change: pictures fly)
        self.previews = {}                       # item -> [_Preview, Texture or None, seq shown]
        self._live_checked = 0.0
        self._sharp_loader = _Sharp()
        self._sharp = collections.OrderedDict()  # path -> Texture of its full picture
        # AI
        self.answer = None                       # ai.ask.Answer filtering the wall (a search / question / person)
        self.answer_shown = 0.0                  # when its sentence appeared
        self._pending_question = None            # asked before the folder was read / looked at
        self.people, self._people_key, self.faces = [], None, {}   # [[(path, face index)...]], face pictures
        self._galaxy, self._galaxy_key = None, None
        self.labels = {}                         # galaxy group name -> _LabelView
        self.room = room
        self.controls = WallControls(self)
        self.panel = WallSettings(self)
        self.people_view = PeopleView(self)
        self.answer_view = AnswerView(self)
        self.grab = _WallGrab(self)
        threading.Thread(target=self._read_folder, daemon=True, name="wall-folder").start()

    def _private(self):
        return self.shell.settings().private_mode

    def _read_folder(self):
        _, found = files.folder_entries(self.folder, files.PICTURE_EXTENSIONS | files.EXTENSIONS)
        self.more = max(0, len(found) - MAX_ITEMS)
        self.all_items = [_Item(self, p) for p in found[:MAX_ITEMS]]
        self.loading = False
        ai_index.indexer().request([i.path for i in self.all_items], self._private())

    def close(self):
        for p in list(self.previews):
            self._stop_preview(p)
        self._sharp_loader.stop()
        for item in self.all_items:
            if item.texture is not None:
                item.texture.delete()
                item.texture = None
        for texture in self._sharp.values():
            texture.delete()
        self._sharp.clear()
        for view in (self.controls, self.panel, self.people_view, self.answer_view, *self.labels.values()):
            view.release()

    def recentered(self):
        self.front_yaw = 0.0

    # -- actions -----------------------------------------------------------------------------------------
    def open(self, item):
        """A picture big in front of you (Previous / Next go through the wall's pictures); a video around you."""
        for p in list(self.previews):
            self._stop_preview(p)
        if item.video:
            self.shell.watch_file(Path(item.path))
        else:
            self.shell.watch_photo(Path(item.path), [i.path for i in self.items if not i.video])

    def menu(self, item):
        path = Path(item.path)
        items = [("Open", Icon.VIDEO if item.video else Icon.PHOTO, lambda: self.open(item), "flat")]
        if item.video:
            items.append(("Timeline in space", Icon.HISTORY, lambda: self.shell.open_timeline(path), "flat"))
        else:
            items.append(("View around me", Icon.SPHERE, lambda: self.shell.watch_photo(path), "flat"))
            items.append(("Use as surroundings", Icon.GALLERY, lambda: self.shell.use_as_surroundings(path), "flat"))
        record = ai_index.lookup(item.path, self._private())
        for n, person in enumerate(self._people()):
            if record and any(key[0] == item.path for key in person):
                items.append((f"Every picture of person {n + 1}", Icon.EYE,
                              lambda n=n: self.show_person(n), "flat"))
        items.append(("More like this", Icon.STAR, lambda: self.more_like(item), "flat"))
        items.append(("Show in its folder (on the PC)", Icon.FOLDER, lambda: files.show_in_folder(path), "flat"))
        title = "Picture" if self._private() else path.name
        self.shell.open_menu(title, items)

    def point_at(self, item, leaving=None):
        if leaving is not None and self.hovered is not leaving:
            return
        if item is not self.hovered:
            self.hovered, self._hovered_since = item, time.monotonic()

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        self.people_open = False

    def toggle_people(self):
        self.people_open = not self.people_open
        self.settings_open = False

    def preview_volume(self, item):
        """A playing video's sound: muted unless you turned it on, and only the one you point / look at."""
        s = self.shell.settings()
        return s.video_volume if s.wall_preview_sound and item is self._focus() else 0.0

    def toggle_sound(self):
        self.set_value("wall_preview_sound", not self.shell.settings().wall_preview_sound)

    def per_page(self):
        return int(self.shell.settings().wall_per_page) or ALL_AT_ONCE

    def pages(self):
        return max(1, math.ceil(len(self.items) / self.per_page()))

    def go_page(self, page):
        """Another page: its pictures fly in where these were (the start of the wall in the middle)."""
        page = max(0, min(self.pages() - 1, page))
        if page != self.page:
            self.page = page
            self.pan, self.velocity = [0.0, 0.0], (0.0, 0.0)

    def set_per_page(self, count):
        """Keeps the first picture you see now on the page that shows."""
        first = self.page * self.per_page()
        self.set_value("wall_per_page", count)
        self.page = first // (count or ALL_AT_ONCE)
        self.pan, self.velocity = [0.0, 0.0], (0.0, 0.0)

    def set_value(self, key, value):
        value = clamp(key, value) if key in LIMITS else value
        if key == "wall_rows":   # each shape keeps its own number of rows / columns
            s = self.shell.settings()
            self.shell.store.update(wall_rows=value, wall_shape_rows={**s.wall_shape_rows, s.wall_shape: value})
            return
        self.shell.store.update(**{key: value})

    def set_shape(self, shape):
        s = self.shell.settings()
        self.shell.store.update(wall_shape=shape, wall_rows=s.wall_shape_rows.get(shape, SHAPE_ROWS.get(shape, 4)))
        self.pan, self.velocity = self._start_pan(), (0.0, 0.0)

    def _start_pan(self):
        """Where a wall starts: its middle in front of you - a tower / column with its first row there."""
        s = self.shell.settings()
        if s.wall_shape not in VERTICAL:
            return [0.0, 0.0]
        self._layout = self.layout(s)
        climb = self._layout[4] / s.wall_size        # tower: its bottom (first pictures) where you look
        return [0.0, climb if s.wall_shape == "tower" else -climb]

    def shuffle(self):
        self._seed = random.random()
        self.set_value("wall_order", "shuffle")

    def recenter_here(self):
        """Back to the start: the wall's middle where you look (the galaxy: back where you started)."""
        self.front_yaw, self.pan, self.velocity = self.shell.look_yaw, self._start_pan(), (0.0, 0.0)
        self.fly[:] = 0.0
        self.galaxy_turn = [0.0, 0.0]

    def reset_settings(self):
        self.shell.store.update(**WALL_DEFAULTS)

    def wheel(self, e):
        """Wheel: zoom toward where you point (the picture there stays under the pointer); Alt: distance.
        The galaxy: fly toward where you point; Alt: picture size."""
        s = self.shell.settings()
        notches = e.delta / 120
        if s.wall_shape == "galaxy":
            if e.alt:
                self.set_value("wall_size", s.wall_size * WHEEL_ZOOM ** notches)
            else:
                d = geometry.ray_direction(-math.radians(e.yaw), math.radians(e.pitch)).astype(np.float32)
                self.fly += self._galaxy_rotation(s)[:3, :3].T @ d * FLY_STEP * notches
            return
        if e.alt:
            self.set_value("wall_distance", s.wall_distance * WHEEL_ZOOM ** notches)
            return
        h0 = s.wall_size
        h1 = clamp("wall_size", h0 * WHEEL_ZOOM ** notches)
        x, y = self._spot(s, e.yaw, e.pitch)
        self.pan = [self.pan[0] + x * (1 / h1 - 1 / h0), self.pan[1] + y * (1 / h1 - 1 / h0)]
        self.set_value("wall_size", h1)

    def _spot(self, s, yaw, pitch):
        """Where the pointer is on the wall, in m from its middle (right, up)."""
        right, up = math.radians(yaw - self.front_yaw), math.radians(pitch - s.wall_tilt)
        if s.wall_shape in FLAT_SHAPES:
            return s.wall_distance * math.tan(right), s.wall_distance * math.tan(up)
        return s.wall_distance * right, s.wall_distance * up

    def drag_by(self, s, d_yaw, d_pitch):
        """The pointer moved (degrees): the pictures follow it (the galaxy turns around you)."""
        if s.wall_shape == "galaxy":
            self.galaxy_turn = [self.galaxy_turn[0] + d_yaw,
                                max(-85.0, min(85.0, self.galaxy_turn[1] + d_pitch))]
            return
        k = s.wall_distance / s.wall_size
        self.pan = self._limit(s, [self.pan[0] + math.radians(d_yaw) * k, self.pan[1] + math.radians(d_pitch) * k])

    def _limit(self, s, pan):
        """A flat wall: any end can come to the middle, no farther. A ring turns forever; a sphere too, and up
        / down as far as its rows go."""
        if self._layout is None or s.wall_shape == "galaxy":
            return pan
        _, _, _, half_width, half_height = self._layout
        h = s.wall_size
        x = pan[0] if s.wall_shape not in FLAT_SHAPES else max(-half_width / h, min(half_width / h, pan[0]))
        y = max(-half_height / h, min(half_height / h, pan[1]))
        if s.wall_shape == "sphere":
            y = max(-85 / math.degrees(h / s.wall_distance), min(85 / math.degrees(h / s.wall_distance), y))
        return [x, y]

    # -- the AI ------------------------------------------------------------------------------------------
    def records(self):
        """{path: Record} of the folder's files the AI has looked at so far."""
        private = self._private()
        out = {}
        for item in self.all_items:
            r = ai_index.lookup(item.path, private)
            if r is not None:
                out[item.path] = r
        return out

    def ai_progress(self):
        """'' when the AI knows the whole folder, else what it's doing."""
        indexer = ai_index.indexer()
        if indexer.state:
            return "AI: " + indexer.state
        done, asked = indexer.progress
        return f"AI looking at the pictures  {done} / {asked}" if done < asked else ""

    def ask(self, question):
        """A search or question (spoken / typed): the wall shows what fits, maybe with an answer."""
        question = question.strip()
        if not question:
            return
        if self.loading or ai_index.indexer().busy():
            self._pending_question = question     # answered again once the AI looked at everything
        try:
            answer = ai_ask.answer(question, self.records())
            answer.question = question
        except Exception:
            log_error(f"ask {question!r}")
            answer = ai_ask.Answer([], "The AI isn't ready yet - try again in a moment", question)
        self._show_answer(answer)

    def _show_answer(self, answer):
        self.answer, self.answer_shown = answer, time.monotonic()
        self.page, self.pan, self.velocity = 0, [0.0, 0.0], (0.0, 0.0)
        self.fly[:] = 0.0
        self._ordered = None

    def clear_answer(self):
        self.answer, self._pending_question = None, None
        self.page, self._ordered = 0, None

    def more_like(self, item):
        record = ai_index.lookup(item.path, self._private())
        if record is None or record.vector is None:
            self._show_answer(ai_ask.Answer([], "The AI hasn't looked at this one yet", "Like this"))
            return
        records = [r for r in self.records().values() if r.vector is not None]
        scores = np.stack([r.vector for r in records]) @ record.vector
        order = np.argsort(-scores)
        paths = [records[i].path for i in order if scores[i] > 0.75][:ai_ask.MAX_RESULTS]
        self._show_answer(ai_ask.Answer(paths, "", "Like " + ("this" if self._private() else Path(item.path).stem)))

    def _people(self):
        """The people the AI found in the folder (faces grouped), biggest first; worked out again as the AI
        finds more."""
        indexer = ai_index.indexer()
        key = (indexer.progress, len(self.all_items), self._private())
        if key != self._people_key:
            self._people_key = key
            from ..ai import faces
            found = [((path, i), f[1]) for path, r in self.records().items() for i, f in enumerate(r.faces)]
            self.people = faces.group(found)
        return self.people

    def show_person(self, n):
        people = self._people()
        if n >= len(people):
            return
        paths = list(dict.fromkeys(key[0] for key in people[n]))
        records = self.records()
        paths.sort(key=lambda p: -(records[p].date if p in records else 0))
        self._show_answer(ai_ask.Answer(paths, f"Person {n + 1}: {len(paths)} pictures", f"Person {n + 1}"))
        self.people_open = False

    def face_picture(self, n):
        """Person n's face (round), made in the background; None until then."""
        people = self._people()
        if n >= len(people):
            return None
        key = people[n][0]
        if key not in self.faces:
            self.faces[key] = None

            def cut():
                try:
                    record = ai_index.lookup(key[0], self._private())
                    box = record.faces[key[1]][0]
                    image, _, _ = ai_index._read(key[0])
                    self.faces[key] = _round_face(image, box)
                except Exception:
                    log_error("face picture")

            threading.Thread(target=cut, daemon=True, name="face-picture").start()
        return self.faces[key]

    # -- memory rooms --------------------------------------------------------------------------------------
    def save_room(self):
        s = self.shell.settings()
        name = (self.answer.title if self.answer else self.title) + " · " + dict(SHAPES)[s.wall_shape]
        asked = self.answer.question if self.answer else ""
        room = {"name": name, "folder": str(self.folder), "question": asked,
                "person_paths": self.answer.paths if self.answer and not asked else None, "environment": s.environment, "environment_turn": s.environment_turn,
                "wall": {k: getattr(s, k) for k in ROOM_KEYS}, "saved": time.time()}
        rooms = [r for r in s.memory_rooms if r.get("name") != name] + [room]
        self.shell.store.update(memory_rooms=rooms)
        self.shell.toast(f"Saved memory room \"{name}\"")

    def apply_room(self, room):
        """Opening a memory room: its settings and surroundings now; its search once the folder is read."""
        self.shell.store.update(**{k: v for k, v in room.get("wall", {}).items() if k in WALL_DEFAULTS},
                                environment=room.get("environment", ""),
                                environment_turn=room.get("environment_turn", 0.0))
        if room.get("person_paths"):
            self._show_answer(ai_ask.Answer(room["person_paths"], "", room.get("question", "")))
        elif room.get("question"):
            self._pending_question = room["question"]

    # -- layout ------------------------------------------------------------------------------------------
    def _sort(self, s):
        key = (s.wall_order, s.wall_show, self._seed, len(self.all_items), id(self.answer))
        if self._ordered == key:
            return
        self._ordered = key
        items = [i for i in self.all_items if s.wall_show == "all" or i.video == (s.wall_show == "videos")]
        if self.answer is not None:         # the search's order (best fit / date first)
            rank = {p: n for n, p in enumerate(self.answer.paths)}
            items = sorted((i for i in items if i.path in rank), key=lambda i: rank[i.path])
        elif s.wall_order == "newest":
            items.sort(key=lambda i: -i.time)
        elif s.wall_order == "shuffle":
            items.sort(key=lambda i: i.path.lower())
            random.Random(self._seed).shuffle(items)
        else:
            items.sort(key=lambda i: Path(i.path).name.lower())
        self.items = items

    def _page(self, s):
        """This page's pictures; the other pages' pictures leave the GPU (a big folder stays light)."""
        per_page = self.per_page()
        self.page = min(self.page, self.pages() - 1)
        key = (self._ordered, self.page, per_page)
        if key == self._paged:
            return
        self._paged = key
        self.shown = self.items[self.page * per_page:(self.page + 1) * per_page]
        keep = set(map(id, self.shown))
        for item in self.all_items:
            if id(item) not in keep:
                if item.texture is not None:
                    item.texture.delete()
                    item.texture = None
                item.pose = None
        if self.hovered not in self.shown:
            self.point_at(None)

    def layout(self, s):
        """(models (n, 4, 4) relative to the wall's middle, widths, heights, half width, half height) - kept
        until something it depends on changes."""
        if s.wall_shape == "galaxy":
            return self._galaxy_layout(s)
        key = (s.wall_shape, s.wall_rows, s.wall_tower_height, s.wall_size, s.wall_distance, s.wall_gap,
               self._ordered,
               self._paged, tuple(i.aspect for i in self.shown))
        if key != self._layout_key:
            self._layout_key, self._layout = key, self._make_layout(s)
        return self._layout

    def _make_layout(self, s):
        if s.wall_shape in VERTICAL:
            return self._vertical_layout(s)
        h, d = s.wall_size, s.wall_distance
        gap = s.wall_gap * h
        n = len(self.shown)
        # few pictures (a search): fewer rows, so they make a wide block in front of you, not a tall column
        count = max(1, min(int(round(s.wall_rows)), round(math.sqrt(max(n, 1) / 1.5)) or 1, n or 1))
        last = None                    # the layout with one row less
        while True:
            per_row = math.ceil(n / count) if n else 1
            rows = [self.shown[i:i + per_row] for i in range(0, n, per_row)]
            models, widths, fits, longest, too_tall = [], [], True, 0.0, False
            for r, row in enumerate(rows):
                offset = ((len(rows) - 1) / 2 - r) * (h + gap)            # this row's middle, up (+) / down (-)
                pitch = math.degrees(offset / d)
                if s.wall_shape == "sphere" and abs(pitch) > 85:          # rows reach the top / bottom of the ball
                    too_tall, pitch = True, math.copysign(85, pitch)
                radius = d * math.cos(math.radians(pitch)) if s.wall_shape == "sphere" else d
                row_widths = [h / i.aspect for i in row]
                length = sum(row_widths) + gap * (len(row) - 1)
                longest = max(longest, length)
                if s.wall_shape != "flat" and length > 2 * math.pi * radius * FULL_CIRCLE:
                    fits = False
                x = -length / 2
                for w in row_widths:
                    middle = x + w / 2
                    x += w + gap
                    if s.wall_shape == "flat":
                        model = geometry.translate(middle, offset, -d)
                    elif s.wall_shape == "ring":
                        model = (geometry.translate(0, offset, 0) @ geometry.rotate_y(-math.degrees(middle / d))
                                 @ geometry.translate(0, 0, -d))
                    else:
                        model = (geometry.rotate_y(-math.degrees(middle / radius)) @ geometry.rotate_x(pitch)
                                 @ geometry.translate(0, 0, -d))
                    models.append(model)
                    widths.append(w)
            if too_tall and last is not None:   # no more room up / down: rows may overlap a bit going around
                return last
            half_height = (len(rows) - 1) / 2 * (h + gap)
            last = (np.array(models, dtype=np.float32).reshape(-1, 4, 4), np.array(widths, dtype=np.float32), h,
                    longest / 2, half_height)
            if fits or too_tall or count >= min(MAX_ROWS, n):
                return last
            count += 1   # a row went all the way around you: one row more

    def _vertical_layout(self, s):
        """Tower: pictures stacked one above the other in columns that stand side by side around you - it rises
        from where you look, and you climb it (drag up / down). `wall_tower_height` pictures per column (more if
        the columns wouldn't fit around you). Column: a flat wall `wall_rows` pictures wide, as tall as it needs,
        read from the top down."""
        h, d = s.wall_size, s.wall_distance
        gap = s.wall_gap * h
        widths_all = [h / i.aspect for i in self.shown]
        n = len(widths_all)
        if not n:
            return np.zeros((0, 4, 4), np.float32), np.zeros(0, np.float32), h, 0.0, 0.0
        models, widths = [None] * n, list(widths_all)
        if s.wall_shape == "tower":
            tall = max(1, int(round(s.wall_tower_height)))
            while True:
                columns = [list(range(i, min(n, i + tall))) for i in range(0, n, tall)]
                column_w = [max(widths_all[i] for i in c) for c in columns]
                around = sum(column_w) + gap * (len(columns) - 1)
                if around <= 2 * math.pi * d * FULL_CIRCLE or tall >= n:
                    break
                tall += 1                      # too many columns to fit around you: taller ones
            stack = min(tall, n)
            x = -around / 2
            for column, cw in zip(columns, column_w):
                middle = x + cw / 2
                x += cw + gap
                for k, i in enumerate(column):         # the first picture at the bottom, the next above it
                    up = (k - (stack - 1) / 2) * (h + gap)
                    models[i] = (geometry.translate(0, up, 0) @ geometry.rotate_y(-math.degrees(middle / d))
                                 @ geometry.translate(0, 0, -d))
            return (np.array(models, dtype=np.float32).reshape(-1, 4, 4), np.array(widths, dtype=np.float32), h,
                    around / 2, (stack - 1) / 2 * (h + gap))
        per_row = max(1, int(round(s.wall_rows)))
        rows = [list(range(i, min(n, i + per_row))) for i in range(0, n, per_row)]
        longest = 0.0
        for r, members in enumerate(rows):
            offset = ((len(rows) - 1) / 2 - r) * (h + gap)
            length = sum(widths_all[i] for i in members) + gap * (len(members) - 1)
            longest = max(longest, length)
            x = -length / 2
            for i in members:
                middle = x + widths_all[i] / 2
                x += widths_all[i] + gap
                models[i] = geometry.translate(middle, offset, -d)
        return (np.array(models, dtype=np.float32).reshape(-1, 4, 4), np.array(widths, dtype=np.float32), h,
                longest / 2, (len(rows) - 1) / 2 * (h + gap))

    def _galaxy_points(self):
        """Galaxy positions (n, 3) of the shown items, groups [(name, member indices)] - from what the AI saw:
        the 3 main directions of the pictures' CLIP vectors (similar pictures land close), groups by k-means.
        Pictures not looked at yet wait far out."""
        private = self._private()
        records = [ai_index.lookup(i.path, private) for i in self.shown]
        known = [n for n, r in enumerate(records) if r is not None and r.vector is not None]
        key = (self._paged, tuple(known), id(self.answer))
        if key == self._galaxy_key:
            return self._galaxy
        self._galaxy_key = key
        n = len(self.shown)
        rng = np.random.default_rng(7)
        far = rng.normal(size=(n, 3)).astype(np.float32)
        points = far / np.linalg.norm(far, axis=1, keepdims=True) * GALAXY_RADIUS * 1.6
        groups = []
        if len(known) >= 4:
            vectors = np.stack([records[k].vector for k in known])
            centered = vectors - vectors.mean(axis=0)
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            coords = centered @ vt[:3].T
            coords /= np.maximum(np.percentile(np.abs(coords), 90, axis=0), 1e-6)
            coords = coords[:, [1, 2, 0]] * np.array([1.0, 0.4, 0.8])    # wider than tall: easier on the neck
            p = coords * GALAXY_RADIUS * 0.6
            p[:, 2] -= GALAXY_RADIUS * 0.55                              # most of it in front of you
            dist = np.linalg.norm(p, axis=1, keepdims=True)
            p = np.where(dist < GALAXY_NEAREST, p / np.maximum(dist, 1e-6) * GALAXY_NEAREST, p)
            points[known] = p.astype(np.float32)
            groups = self._galaxy_groups(vectors, known)
        self._galaxy = (points, groups)
        return self._galaxy

    @staticmethod
    def _galaxy_groups(vectors, known):
        """k-means on the vectors: [(name, indices into shown)] - named by the AI."""
        from ..ai import clip
        k = max(2, min(9, len(known) // 7))
        if len(known) < 8:
            return []
        rng = np.random.default_rng(1)
        centers = vectors[rng.choice(len(vectors), k, replace=False)]
        for _ in range(12):
            member = np.argmax(vectors @ centers.T, axis=1)
            for c in range(k):
                if np.any(member == c):
                    m = vectors[member == c].mean(axis=0)
                    centers[c] = m / max(np.linalg.norm(m), 1e-8)
        groups, used = [], set()
        for c in range(k):
            members = [known[i] for i in np.nonzero(member == c)[0]]
            if len(members) < 3:
                continue
            try:
                name = clip.label_for(centers[c])
            except Exception:
                return []
            if name in used:
                continue
            used.add(name)
            groups.append((name, members))
        return groups

    def _galaxy_rotation(self, s):
        return (geometry.rotate_y(-self.front_yaw) @ geometry.rotate_x(self.galaxy_turn[1])
                @ geometry.rotate_y(-self.galaxy_turn[0]))

    def _galaxy_layout(self, s):
        points, _ = self._galaxy_points()
        widths = np.array([s.wall_size / i.aspect for i in self.shown], np.float32)
        return points, widths, s.wall_size, 0.0, 0.0

    def _moved_wall(self, s):
        """The pan as a matrix: a flat wall slides, a ring turns and goes up / down, a sphere turns both ways."""
        h, d = s.wall_size, s.wall_distance
        x, y = self.pan[0] * h, self.pan[1] * h
        if s.wall_shape in FLAT_SHAPES:
            return geometry.translate(x, y, 0)
        if s.wall_shape in AROUND_SHAPES:
            return geometry.translate(0, y, 0) @ geometry.rotate_y(-math.degrees(x / d))
        return geometry.rotate_x(math.degrees(y / d)) @ geometry.rotate_y(-math.degrees(x / d))

    def _world(self, s):
        """(models (n, 4, 4) in the room, widths, height) of the shown pictures this frame."""
        if s.wall_shape == "galaxy":
            points, widths, h, _, _ = self.layout(s)
            rot = self._galaxy_rotation(s)[:3, :3].astype(np.float32)
            placed = ((points - self.fly) * (s.wall_space / GALAXY_RADIUS)) @ rot.T   # the space's size
            models = _facing(placed)
            # far stars are drawn bigger (up to 2.5x), so the galaxy stays readable around you
            k = np.clip(np.linalg.norm(placed, axis=1) / GALAXY_GROW_FROM, 1.0, 2.5)[:, None, None]
            models[:, :3, :3] *= k
            return models, widths, h
        models, widths, h, _, _ = self.layout(s)
        front = geometry.rotate_y(-self.front_yaw) @ geometry.rotate_x(s.wall_tilt) @ self._moved_wall(s)
        return np.matmul(front.astype(np.float32), models), widths, h

    # -- every frame ------------------------------------------------------------------------------------
    def _upload(self):
        budget = UPLOADS_PER_FRAME
        for item in self.shown:
            if item.texture is not None:
                continue
            image, seconds = THUMBNAILS.get(item.path)
            if image is None:
                continue
            item.texture = Texture()
            item.texture.upload(self._badged(image, seconds) if item.video else image.convert("RGBA"))
            item.aspect = image.height / image.width
            budget -= 1
            if budget == 0:
                return

    @staticmethod
    def _badged(image, seconds):
        """A video's thumbnail with ▶ and its length in the corner."""
        image = image.convert("RGBA")
        d = ImageDraw.Draw(image)
        size = max(18, image.height // 9)
        text = _time(seconds) if seconds else ""
        icon, label = theme.font(size, icons=True), theme.font(size)
        width = size * 1.6 + (d.textlength(text, font=label) + size * 0.4 if text else 0)
        x1, y1 = image.width - size * 0.5, image.height - size * 0.5
        x0, y0 = x1 - width, y1 - size * 1.6
        d.rounded_rectangle((x0, y0, x1, y1), radius=size * 0.5, fill=(10, 12, 16, 200))
        d.text((x0 + size * 0.8, (y0 + y1) / 2), Icon.PLAY, font=icon, fill=theme.FG, anchor="mm")
        if text:
            d.text((x0 + size * 1.5, (y0 + y1) / 2), text, font=label, fill=theme.FG, anchor="lm")
        return image

    def _focus(self):
        """The item you point at, else the one you look at (look to pick)."""
        return self.hovered or self.looked

    def _stop_preview(self, item):
        entry = self.previews.pop(item, None)
        if entry is not None:
            entry[0].stop()
            if entry[1] is not None:
                entry[1].delete()

    def _playing(self, s, now, centers):
        """Which videos play: the pointed / looked at one (after a moment), and in the live wall the ones
        nearest where you look. Returns {item: texture} of those with a picture."""
        wanted = set()
        focus = self._focus()
        if focus is not None and focus.video and s.wall_previews and \
                now - max(self._hovered_since, self._looked_since if focus is self.looked else 0) > PREVIEW_DELAY:
            wanted.add(focus)
        if s.wall_live:
            look = self._look_direction()
            videos = [(float(centers[i] @ look / max(np.linalg.norm(centers[i]), 1e-6)), item)
                      for i, item in enumerate(self.shown) if item.video]
            videos.sort(key=lambda v: -v[0])
            wanted.update(item for _, item in videos[:LIVE_MAX])
        for item in list(self.previews):
            if item not in wanted:
                self._stop_preview(item)
        out = {}
        for item in wanted:
            entry = self.previews.get(item)
            if entry is None:
                live = s.wall_live and item is not focus
                preview = _Preview(item.path, item.aspect, lambda item=item: self.preview_volume(item),
                                   rate=lambda item=item: 1.0 if item is self._focus() or not
                                   self.shell.settings().wall_live else LIVE_SLOW,
                                   width=LIVE_WIDTH if live else PREVIEW_WIDTH)
                preview.start()
                entry = self.previews[item] = [preview, None, -1]
            preview, texture, seq = entry
            if preview.frame is not None and preview.seq != seq:
                if texture is None:
                    texture = entry[1] = Texture()
                texture.upload(preview.frame)
                entry[2] = preview.seq
            if entry[1] is not None:
                out[item] = entry[1]
        return out

    def _look_direction(self):
        sh = self.shell
        return geometry.ray_direction(-math.radians(sh.look_yaw), math.radians(sh.look_pitch)).astype(np.float32)

    def _look_pick(self, s, now, centers, sizes):
        """Look to pick: the picture in the middle of your view; looking long enough opens it."""
        if not s.wall_look_pick or now < self._look_rest:
            self.looked = None
            return 0.0
        look = self._look_direction()
        far = np.maximum(np.linalg.norm(centers, axis=1), 1e-6)
        angle = np.arccos(np.clip(centers @ look / far, -1, 1))
        inside = angle < np.arctan(sizes * 0.4 / far)
        item = self.shown[int(np.argmin(np.where(inside, angle, 9)))] if inside.any() else None
        if item is not self.looked:
            self.looked, self._looked_since = item, now
        if item is None:
            return 0.0
        progress = (now - self._looked_since) / LOOK_PICK_SECONDS
        if progress >= 1.0:
            self.looked, self._look_rest = None, now + 2.5   # a moment before it picks again
            self.open(item)
            return 0.0
        return progress

    def _sharp_texture(self, item, wanted):
        """The full picture's texture if loaded; asks for it when `wanted`."""
        texture = self._sharp.get(item.path)
        if texture is not None:
            self._sharp.move_to_end(item.path)
            return texture
        pixels = self._sharp_loader.ready.pop(item.path, None)
        if pixels is not None:
            texture = Texture()
            texture.upload(pixels)
            self._sharp[item.path] = texture
            while len(self._sharp) > SHARP_KEEP:
                path, old = self._sharp.popitem(last=False)
                old.delete()
                self._sharp_loader.forget(path)
            return texture
        if wanted:
            self._sharp_loader.want(item.path, item.aspect)
        return None

    def _flying(self, i, item, model, now, changed):
        """Pictures fly to their new places when what shows changes."""
        if changed:
            item.fly_from, item.fly_start = item.pose, now
            if item.fly_from is None:             # new: comes from far behind its place
                item.fly_from = model.copy()
                item.fly_from[:3, 3] *= 3.0
        if item.fly_from is not None:
            t = (now - item.fly_start) / FLY_SECONDS
            if t >= 1.0:
                item.fly_from = None
            else:
                e = 1 - (1 - t) ** 3
                model = model.copy()
                model[:3, 3] = item.fly_from[:3, 3] * (1 - e) + model[:3, 3] * e
        item.pose = model
        return model

    def surfaces(self, now):
        s = self.shell.settings()
        dt = min(0.1, now - self._last_frame) if self._last_frame else 0.0
        self._last_frame = now
        if self.loading:
            return self._ui(now)
        if self.room is not None:
            room, self.room = self.room, None
            self.apply_room(room)
        if self._pending_question and not ai_index.indexer().busy():
            question, self._pending_question = self._pending_question, None
            self.ask(question)
        self._sort(s)
        self._page(s)
        self._upload()
        if not self.shown:
            return self._ui(now)
        if self.velocity != (0.0, 0.0) and not self.grab.dragging:     # gliding after a quick drag
            vx, vy = self.velocity
            self.pan = self._limit(s, [self.pan[0] + vx * dt, self.pan[1] + vy * dt])
            slow = math.exp(-GLIDE_SLOWDOWN * dt)
            self.velocity = (vx * slow, vy * slow) if math.hypot(vx, vy) * slow > 0.05 else (0.0, 0.0)
        world, widths, h = self._world(s)
        key = (s.wall_shape, self._paged, self._layout_key if s.wall_shape != "galaxy" else self._galaxy_key)
        changed, self._last_key = key != self._last_key, key
        centers = world[:, :3, 3]
        far = np.linalg.norm(centers, axis=1) + 1e-6
        scale = np.linalg.norm(world[:, :3, 0], axis=1)        # the galaxy draws far pictures bigger
        pointer = self.shell.pointer
        direction = np.asarray(pointer.direction(), dtype=np.float32) if pointer.active else None
        if direction is not None:
            reach = np.arcsin(np.minimum(1.0, 0.5 * np.hypot(widths, h) * scale * s.wall_hover_zoom / far))                 + HIT_MARGIN
            near = (centers @ direction) / far > np.cos(np.minimum(reach, math.pi))
        else:
            near = np.zeros(len(self.shown), dtype=bool)
        big = h * scale / far > SHARP_ANGLE
        look_progress = self._look_pick(s, now, centers, np.maximum(widths, h) * scale)
        playing = self._playing(s, now, centers)
        focus = self._focus()

        out = []
        for i, item in enumerate(self.shown):
            target = s.wall_hover_zoom if item is focus else 1.0
            item.grow += (target - item.grow) * min(1.0, GROW_SPEED * dt) if dt else target - item.grow
            model = self._flying(i, item, world[i], now, changed)
            w, height = float(widths[i]), h
            if item.grow > 1.001:
                g = item.grow
                lift = 0.1 * (s.wall_distance if s.wall_shape != "galaxy" else far[i]) * (g - 1) / g
                model = model @ geometry.translate(0, 0, lift)   # also nearer
                w, height = w * g, height * g
            texture = playing.get(item, item.texture)
            if item not in playing and not item.video and item.texture is not None:
                sharp = self._sharp_texture(item, wanted=big[i] or (item is focus and item.grow > 1.15))
                texture = sharp or texture
            if item is focus:
                edge = 0.03 * max(1.0, item.grow)
                out.append(Surface("wall-frame", model @ geometry.translate(0, 0, -0.005), w + edge, height + edge,
                                   color=HOVER_FRAME if item is self.hovered else LOOK_FRAME))
                if item is self.looked and look_progress > 0:      # look to pick: a bar filling up under it
                    bar_w = w * look_progress
                    out.append(Surface("wall-look", model @ geometry.translate(-(w - bar_w) / 2, -height / 2 - 0.04,
                                                                                0.005),
                                       bar_w, 0.025, color=LOOK_FRAME))
            out.append(_PictureSurface("wall:" + item.path, model, w, height, texture=texture, color=EMPTY_COLOR,
                                       target=item, near=bool(near[i])))
        if s.wall_shape == "galaxy":
            out += self._label_surfaces(s, world)
        return out + self._ui(now)

    def _label_surfaces(self, s, world):
        """The galaxy's group names, floating above each group."""
        _, groups = self._galaxy_points()
        out = []
        for name, members in groups:
            center = world[members, :3, 3].mean(axis=0)
            top = world[members, 1, 3].max() + s.wall_size
            point = np.array([[center[0], top, center[2]]], np.float32)
            distance = max(1.0, float(np.linalg.norm(point)))
            view = self.labels.get(name)
            if view is None or not 0.67 < view.made_at / distance < 1.5:   # names stay readable at any distance
                if view is not None:
                    view.release()
                view = self.labels[name] = _LabelView(name.capitalize(), distance)
            view.refresh()
            out.append(Surface("wall-label:" + name, _facing(point)[0], view.width, view.height,
                               texture=view.texture, layer=INTERFACE))
        return out

    def _ui(self, now):
        out = []
        front = geometry.rotate_y(-self.front_yaw)
        if self.answer is not None and self.answer.text and now - self.answer_shown < ANSWER_SECONDS:
            self.answer_view.refresh()
            model = front @ geometry.translate(0, 0.55, -1.6) @ geometry.rotate_x(-8)
            out.append(Surface("wall-answer", model, self.answer_view.width, self.answer_view.height,
                               texture=self.answer_view.texture, layer=INTERFACE))
        if self.shell.pointer.active:
            self.controls.refresh()
            model = front @ geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ geometry.tilt(DOCK_TILT)
            out.append(Surface("wall-controls", model, self.controls.width, self.controls.height,
                               texture=self.controls.texture, layer=INTERFACE, target=self.controls,
                               data={"ui": True}))
            above = None
            if self.settings_open:
                above = ("wall-settings", self.panel)
            elif self.people_open:
                above = ("wall-people", self.people_view)
            if above is not None:
                name, view = above
                view.refresh()
                x = self.controls.width / 2 - view.width / 2 if view is self.panel else 0.0
                y = self.controls.height / 2 + PANEL_GAP + view.height / 2
                out.append(Surface(name, model @ geometry.translate(x, y, 0.01), view.width, view.height,
                                   texture=view.texture, layer=INTERFACE, target=view, data={"ui": True}))
        return out


class _WallGrab:
    """Pressing a picture or empty space and dragging: the pictures follow the pointer. A press on a picture
    that hardly moves is a click (left: open it, right: its menu)."""

    def __init__(self, wall):
        self.wall = wall
        self._press = None             # (item or None, button, last yaw, last pitch, when)
        self.dragging = False

    def on_pointer(self, e, item=None):
        wall = self.wall
        s = wall.shell.settings()
        if e.kind == "press":
            self._press = (item, e.button, e.yaw, e.pitch, time.monotonic())
            self.dragging = False
            wall.velocity = (0.0, 0.0)
        elif e.kind == "drag" and self._press is not None:
            item0, button, yaw, pitch, when = self._press
            if not self.dragging and math.hypot(e.yaw - e.start_yaw, e.pitch - e.start_pitch) < DRAG_DEGREES:
                return
            self.dragging = True
            now = time.monotonic()
            before = list(wall.pan)
            wall.drag_by(s, e.yaw - yaw, e.pitch - pitch)
            step = max(0.011, now - when)            # (pointer moves come once a frame at most)
            v = ((wall.pan[0] - before[0]) / step, (wall.pan[1] - before[1]) / step)
            vx, vy = (0.5 * a + 0.5 * b for a, b in zip(wall.velocity, v))   # smoothed
            speed = math.hypot(vx, vy)
            wall.velocity = (vx, vy) if speed <= GLIDE_MAX else (vx * GLIDE_MAX / speed, vy * GLIDE_MAX / speed)
            self._press = (item0, button, e.yaw, e.pitch, now)
        elif e.kind == "release" and self._press is not None:
            item0, button, _, _, when = self._press
            self._press = None
            if self.dragging:
                self.dragging = False
                if time.monotonic() - when > 0.08:   # held still before letting go: no glide
                    wall.velocity = (0.0, 0.0)
            elif item0 is not None and e.on_surface:
                wall.velocity = (0.0, 0.0)
                if button == "left":
                    wall.open(item0)
                elif button == "right":
                    wall.menu(item0)


class _LabelView(View):
    """A galaxy group's name, floating."""

    def __init__(self, text, distance):
        super().__init__(distance=distance)
        self.text, self.made_at = text, distance

    def state(self):
        return self.text

    def build(self):
        return Row([Label(self.text, size=theme.TITLE, color=theme.FG, h=theme.ROW, indent=8)],
                   pad=16, bg=(16, 18, 24, 170), radius=theme.RADIUS_CARD)


class AnswerView(View):
    """The AI's answer, big, above the wall ("Last time in Lisbon: 2 June 2025")."""

    def __init__(self, wall):
        super().__init__(distance=1.6)
        self.wall = wall

    def state(self):
        a = self.wall.answer
        return a and a.text

    def build(self):
        text = self.wall.answer.text if self.wall.answer else ""
        return Row([Label(Icon.STAR, size=theme.TITLE, color=theme.ACCENT_LIGHT, icon=True, w=70, h=theme.ROW + 20),
                    Label(text, size=theme.TITLE, h=theme.ROW + 20)], pad=24, gap=8, bg=theme.BG,
                   radius=theme.RADIUS_PANEL, border=theme.EDGE)


class PeopleView(View):
    """The people the AI found in the folder: their faces; a click shows every picture of that person."""
    SHOWN = 8

    def __init__(self, wall):
        super().__init__(distance=DOCK_DISTANCE)
        self.wall = wall

    def state(self):
        w = self.wall
        people = w._people()
        return (len(people), tuple(len(p) for p in people[:self.SHOWN]),
                tuple(w.face_picture(n) is not None for n in range(min(self.SHOWN, len(people)))), w.ai_progress())

    def build(self):
        w = self.wall
        people = w._people()
        cards = []
        for n, person in enumerate(people[:self.SHOWN]):
            pictures = len({k[0] for k in person})
            cards.append(Card(f"person:{n}", f"Person {n + 1}", w.face_picture(n), 190, 250, icon=Icon.EYE,
                              subtitle=f"{pictures} pictures", on_click=lambda n=n: w.show_person(n)))
        progress = w.ai_progress()
        if not cards:
            text = progress or "No faces found here (faces need to be clear and not too small)"
            cards = [Label(text, size=theme.SMALL, color=theme.MUTED, w=1200, h=theme.ROW, align="center")]
        rows = [Row([Label("People", size=theme.TITLE, color=theme.ACCENT, indent=24), Spacer(flex=1),
                     Label(progress, size=theme.SMALL - 6, color=theme.MUTED, h=theme.ROW)], w=1640,
                    h=int(theme.ROW * 1.2)),
                Row(cards, gap=16, h=260 if len(cards) > 1 or people else theme.ROW)]
        return Panel(rows, pad=20, gap=8)


class WallControls(View):
    """  Size ━━━●━━━━━━━━━━   ◁ Page 2 / 3 ▷   Zoom ━━━●━━━━━━━━
         Folder · 48 pictures      Sphere  Ring  Flat  Galaxy  🔇     ⚙  ⟳  🖱 PC  ✕ Exit
         🔎 beach · 12 ✕                           Ask   People   Rooms   (AI looking 30 / 120)"""

    def __init__(self, wall):
        super().__init__(distance=DOCK_DISTANCE)
        self.wall = wall

    def state(self):
        w, s = self.wall, self.wall.shell.settings()
        a = w.answer
        return (w.settings_open, w.people_open, s.wall_shape, s.private_mode, len(w.items), w.loading, s.wall_size,
                w.page, w.pages(), s.wall_hover_zoom, w.hovered.path if w.hovered else None, self.ctx.drag_start,
                s.wall_preview_sound, a and (a.title, len(a.paths)), w.ai_progress(), len(s.memory_rooms),
                s.voice_control, w.shell.voice is not None and w.shell.voice.listening)

    def build(self):
        w, s = self.wall, self.wall.shell.settings()
        h = theme.BAR - 8
        if s.private_mode:
            title = "Pictures"
        elif w.hovered is not None:
            title = Path(w.hovered.path).name
        else:
            title = w.title
        videos = sum(i.video for i in w.items)
        if w.loading:
            count = "Reading the folder..."
        elif not w.items:
            count = "Nothing to show here" + ("  ·  ⚙ Show: All" if s.wall_show != "all" else "")
        else:
            pictures = len(w.items) - videos
            parts = [f"{pictures} picture{'s' * (pictures != 1)}" if pictures else "",
                     f"{videos} video{'s' * (videos != 1)}" if videos else "",
                     f"the first {MAX_ITEMS}" if w.more else ""]
            count = "  ·  ".join(p for p in parts if p)
        about_w = ABOUT_WIDTH    # (the 6 shapes and the buttons on the right need the rest of the bar)
        about = Column([Label(title, size=theme.SMALL, h=int(theme.ROW * 0.8), w=about_w, indent=16),
                        Label(count, size=theme.SMALL - 6, color=theme.MUTED, h=int(theme.ROW * 0.7), w=about_w,
                              indent=16)], w=about_w, h=h)

        def button(key, text=None, icon=None, action=None, style="flat", selected=False, width=None, height=h):
            return Button(key, text, icon, h=height, w=width, style=style, selected=selected, size=theme.TEXT - 4,
                          on_click=action)

        shapes = Row([Button("shape:" + key, name, h=h, style="flat", selected=s.wall_shape == key,
                             size=theme.TEXT - 8, on_click=lambda key=key: w.set_shape(key)) for key, name in SHAPES]
                     , gap=6, h=h)
        right = Row([button("settings", icon=Icon.SETTINGS, action=w.toggle_settings, selected=w.settings_open,
                            width=h + 10),
                     button("recenter", icon=Icon.RECENTER, action=w.recenter_here, width=h + 10),
                     button("mouse", icon=Icon.MOUSE, action=lambda: w.shell.dock_action("mouse_to_pc"), width=h + 10),
                     button("exit", "Exit", Icon.CLOSE, action=w.shell.stop_wall, style="danger")],
                    h=h, gap=4)
        lo, hi = LIMITS["wall_size"]
        pager_w, arrow = 420, theme.ROW + 20
        half = (CONTROLS_WIDTH - pager_w - 48) // 2
        pages = w.pages()
        pager = Row([Button("page-back", icon=Icon.PREVIOUS, h=theme.ROW, w=arrow, style="flat", size=theme.TEXT - 6,
                            muted=w.page == 0,
                            on_click=(lambda: w.go_page(w.page - 1)) if w.page > 0 else None),
                     Label(f"Page {w.page + 1} / {pages}", size=theme.SMALL, w=pager_w - 2 * arrow - 12, h=theme.ROW,
                           align="center"),
                     Button("page-next", icon=Icon.NEXT, h=theme.ROW, w=arrow, style="flat", size=theme.TEXT - 6,
                            muted=w.page >= pages - 1,
                            on_click=(lambda: w.go_page(w.page + 1)) if w.page < pages - 1 else None)],
                    w=pager_w, h=theme.ROW, gap=6)
        size = Slider("size", "Size", math.log(s.wall_size), math.log(lo), math.log(hi), w=half,
                      text=lambda value: f"{math.exp(value):.2f} m",
                      on_change=lambda value: w.set_value("wall_size", math.exp(value)))
        zlo, zhi = LIMITS["wall_hover_zoom"]
        zoom = Slider("hover-zoom", "Zoom", s.wall_hover_zoom, zlo, zhi, "{:.1f}x", w=half,
                      on_change=lambda value: w.set_value("wall_hover_zoom", value))

        # The AI row: what the wall is filtered by, and the AI's buttons
        small = theme.ROW
        listening = w.shell.voice is not None and w.shell.voice.listening
        a = w.answer
        if a is not None:
            found = f"{len(w.items)} found" if w.items else "nothing found"
            label = "Pictures" if s.private_mode and a.title.startswith("Like") else a.title
            chip = Row([Label(Icon.ZOOM, size=theme.SMALL, color=theme.ACCENT_LIGHT, icon=True, w=50, h=small),
                        Label(f"{label}  ·  {found}", size=theme.SMALL - 4, h=small, w=700),
                        Button("clear", "Show all", Icon.CLOSE, h=small, style="normal", size=theme.TEXT - 8,
                               on_click=w.clear_answer)], h=small, gap=6)
        else:
            chip = Label("Press Talk and say \"beach\" or \"when was I last in Lisbon\"",
                         size=theme.SMALL - 8, color=theme.MUTED, h=small, w=980, indent=16)
        progress = w.ai_progress()
        ai_row = Row([chip, Spacer(flex=1),
                      Label(progress, size=theme.SMALL - 8, color=theme.ACCENT_LIGHT, h=small, w=420 if progress else 0,
                            align="right"),
                      button("sound", icon=Icon.VOLUME if s.wall_preview_sound else Icon.MUTE, action=w.toggle_sound,
                             selected=s.wall_preview_sound, width=small + 10, height=small),
                      *([button("talk", "Talk", Icon.MIC, action=w.shell.listen, height=small,
                                selected=listening, style="normal" if listening else "flat")]
                        if s.voice_control else []),
                      button("people", "People", Icon.EYE, action=w.toggle_people, selected=w.people_open,
                             height=small),
                      button("rooms", "Memories", Icon.STAR, action=lambda: w.shell.rooms_menu(w), height=small)],
                     w=CONTROLS_WIDTH, h=small, gap=8)
        return Panel([Row([size, pager, zoom], w=CONTROLS_WIDTH, h=theme.ROW, gap=24),
                      Row([about, Spacer(flex=1), shapes, Spacer(flex=1), right], w=CONTROLS_WIDTH, h=h),
                      ai_row], pad=20, gap=6)


class WallSettings(View):
    """⚙ of the photo wall: what to show, order, rows, distance, gap, tilt, video playing, look to pick."""

    def __init__(self, wall):
        super().__init__(distance=DOCK_DISTANCE)
        self.wall = wall

    def state(self):
        s = self.wall.shell.settings()
        return tuple(getattr(s, k) for k in WALL_DEFAULTS) + (self.ctx.drag_start,)

    def build(self):
        wall, s = self.wall, self.wall.shell.settings()
        w = PANEL_WIDTH

        def slider(key, label, fmt, scale=1.0, whole=False):
            lo, hi = LIMITS[key]
            return Slider(key, label, getattr(s, key) * scale, lo * scale, hi * scale, fmt, w=w,
                          on_change=lambda value: wall.set_value(key, round(value) if whole else value / scale))

        def log_slider(key, label):
            """Logarithmic: as many fine steps near as far."""
            lo, hi = LIMITS[key]
            return Slider(key, label, math.log(getattr(s, key)), math.log(lo), math.log(hi), w=w,
                          text=lambda value: f"{math.exp(value):.2f} m",
                          on_change=lambda value: wall.set_value(key, math.exp(value)))

        def choices(label, options, current, pick):
            buttons = [Button(f"{label}:{key}", name, h=theme.ROW - 8, flex=1, size=theme.TEXT - 6,
                              selected=current == key, on_click=lambda key=key: pick(key)) for key, name in options]
            return Row([Label(label, w=int(w * 0.2), indent=24), Row(buttons, gap=6, flex=1, h=theme.ROW - 8)],
                       w=w, h=theme.ROW, gap=8)

        def toggle(key, label):
            return Toggle(key, label, getattr(s, key), w=w, on_change=lambda value: wall.set_value(key, value))

        def hint(text):
            return Label(text, size=theme.SMALL - 6, color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24)

        rows = [Row([Label("Photo wall", size=theme.TITLE, color=theme.ACCENT, indent=24), Spacer(flex=1),
                     Button("reset", "Reset", h=theme.ROW - 8, style="flat", size=theme.TEXT - 6,
                            on_click=wall.reset_settings)], w=w, h=int(theme.ROW * 1.2), gap=6),
                choices("Show", SHOWS, s.wall_show, lambda key: wall.set_value("wall_show", key)),
                choices("Order", ORDERS, s.wall_order,
                        lambda key: wall.shuffle() if key == "shuffle" else wall.set_value("wall_order", key)),
                choices("Per page", PER_PAGE, int(s.wall_per_page), wall.set_per_page),
                (slider("wall_tower_height", "Height (pictures)", "{:.0f}", whole=True) if s.wall_shape == "tower"
                 else slider("wall_rows", "Columns" if s.wall_shape == "column" else "Rows", "{:.0f}", whole=True)),
                (log_slider("wall_space", "Space size") if s.wall_shape == "galaxy"
                 else log_slider("wall_distance", "Distance")),
                slider("wall_gap", "Gap", "{:.0f}%", 100),
                slider("wall_tilt", "Tilt (up / down)", "{:+.0f}°"),
                toggle("wall_previews", "Play the video you point at"),
                toggle("wall_live", "Live wall"),
                hint("All videos play slowly; the one you point or look at plays for real"),
                choices("Behind", (("black", "Black"), ("mine", "My surroundings"), ("stars", "Stars")),
                        s.wall_background, lambda key: wall.set_value("wall_background", key)),
                toggle("wall_look_pick", "Look to pick"),
                hint("Look at a picture for a moment and it opens (no mouse needed)"),
                hint("Drag to move  ·  wheel: zoom (galaxy: fly)  ·  Alt + wheel: distance (galaxy: picture size)")]
        return Panel(rows, pad=20, gap=4)
