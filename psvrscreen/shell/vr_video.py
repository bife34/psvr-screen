"""VR videos: noticing them in browser windows, and watching them around you.

VideoDetector (background thread) reads the address of every browser window on YouTube or with VR in its
title and asks the site (video/sites.py) whether that is a VR video. If it is, a "Watch in VR" button appears
under the window (in VR) or above the dock (a browser on the main screen).

ImmersiveVideo is the video mode: the video plays on a sphere around you (render/video_sphere.py) instead
of the room and the windows; its controls float below in front of the video while the pointer is in VR, and
their ⚙ opens the video settings (view, 3D, size, distance, brightness, volume - all sliders).
Moving the video: grab empty space with the left button and drag (it follows the pointer, also up / down),
"Recenter here" (its middle goes where you look), or the Tilt slider - like HereSphere / DeoVR / Skybox.
Two sources: the site's stream, played here (the browser's own playback is paused), or - when the site's
stream can't be played - the browser window's picture (WindowPlayer; the browser plays it, with its sound).
"""
import dataclasses
import math
import threading
import time
from pathlib import Path

import numpy as np

from ..core import geometry
from ..core.settings import LIMITS, Settings, clamp, shown_title
from ..render.video_sphere import DepthTexture, VideoTexture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Label, Panel, Row, SeekBar, Slider, Spacer, Toggle, Widget
from ..video import files, recognize, sites
from ..video.player import VideoPlayer
from ..win.capture import WindowCapture
from .depth_job import DepthJob
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
CHECK_SECONDS = 2.0
CONTROLS_SECONDS = 4.0             # controls stay this long after the pointer last moved
CONTROLS_WIDTH = 1580
SIDE_WIDTH = 560                   # controls: title on the left, settings / mouse / exit on the right
PANEL_WIDTH = 1120                 # video settings
PANEL_GAP = 0.02                   # m between the controls and the settings above them
SKIP_SECONDS = 10
PROJECTION_NAMES = {"equi": "360°", "equi180": "180°", "mesh": "360°", "screen": "Screen", "fisheye": "Fisheye",
                    None: "VR"}
WINDOW_LOOKS = 3                   # window source: frames looked at (1 s apart) to recognise the layout
WINDOW_RECHECK_SECONDS = 6         # a window source is looked at again this often (did it go full screen?)
WINDOW_SAME_PICTURE = 0.5          # stricter than for streams: a web page's own layout can look a bit 3D
STEREO_NAMES = {"mono": "2D", "top_bottom": "3D ↕", "side_by_side": "3D ↔"}
VIDEO_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Settings) if f.name.startswith("video_")}
DOME_MAP_PX = 300                  # the dome map in the video settings
LOOK_PAD_W, LOOK_PAD_H = 340, 170  # 3D photo: the Look-from pad
VOLUME_WIDTH = 380                 # the volume slider next to the time line
PLACE_LIMIT = 0.9                  # you can go this far from the middle (part of the radius)
EYE_KEYS = ("video_ipd", "video_eye_height", "video_eye_roll", "video_eye_scale")   # kept per video
ALIGN_WIDTH = 960                  # Auto-align looks at each eye's picture this wide
ALIGN_SIGNS = (-1.0, -1.0, -1.0)   # how Auto-align's findings map onto the sliders (height, turn, size)


def measure_eyes(frame, stereo, crop=(0.0, 0.0, 1.0, 1.0)):
    """How the right eye's picture is off against the left one's in a 3D frame (a video Frame or a BGRA
    picture): (vertical px at the middle, turn degrees, size factor, eye picture width px, matches). Raises
    ValueError when there's too little detail to tell."""
    import cv2
    gray = frame.y if hasattr(frame, "y") else cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    h, w = gray.shape[:2]
    gray = gray[int(crop[1] * h):int(crop[3] * h), int(crop[0] * w):int(crop[2] * w)]
    h, w = gray.shape[:2]
    left, right = ((gray[:, :w // 2], gray[:, w // 2:w // 2 * 2]) if stereo == "side_by_side"
                   else (gray[:h // 2], gray[h // 2:h // 2 * 2]))
    k = min(1.0, ALIGN_WIDTH / left.shape[1])
    left, right = (np.ascontiguousarray(cv2.resize(e, None, fx=k, fy=k, interpolation=cv2.INTER_AREA))
                   for e in (left, right))
    orb = cv2.ORB_create(4000)
    kl, dl = orb.detectAndCompute(left, None)
    kr, dr = orb.detectAndCompute(right, None)
    if dl is None or dr is None:
        raise ValueError("no details to compare")
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(dr, dl)
    if len(matches) < 12:
        raise ValueError("too few details match")
    pr = np.float32([kr[m.queryIdx].pt for m in matches])
    pl = np.float32([kl[m.trainIdx].pt for m in matches])
    # (3D pictures differ left-right by depth on purpose: only up-down, turn and size are errors - so the fit
    # is done on the up-down part, with a turn / size that's the same everywhere)
    m, inliers = cv2.estimateAffinePartial2D(pr, pl, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if m is None or inliers is None or inliers.sum() < 10:
        raise ValueError("the eyes' pictures don't match")
    scale = float(np.hypot(m[0, 0], m[1, 0]))
    turn = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
    cx, cy = left.shape[1] / 2, left.shape[0] / 2
    dy = float(m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy) / k
    return dy, turn, scale, left.shape[1] / k, int(inliers.sum())


def is_browser_video(exe, title):
    """A browser window worth asking: on YouTube, or a page that talks about VR."""
    title = title or ""
    return (exe or "").lower() in BROWSERS and ("youtube" in title.lower() or recognize.looks_vr(title))


class VideoDetector(threading.Thread):
    """Set `candidates` = [(hwnd, title)] (see is_browser_video); read `found` = {hwnd: VideoInfo} for the
    ones showing a VR video."""

    def __init__(self):
        super().__init__(daemon=True, name="vr-video-detector")
        self.candidates = []
        self.found = {}
        self._infos = {}                 # page address -> VideoInfo or None (not a VR video)
        self._window_checks = {}         # hwnd -> (when, shows a VR picture) for window sources
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        from ..win.browser import BrowserReader
        reader = BrowserReader()
        try:
            while not self._stop.wait(CHECK_SECONDS):
                found = {}
                for hwnd, title in list(self.candidates):
                    url = reader.page_address(hwnd)
                    if url is None:
                        continue
                    if url not in self._infos:
                        try:
                            self._infos[url] = sites.probe(url, title)
                        except Exception:
                            continue     # network / site trouble: try again next time
                    info = self._infos[url]
                    if info is not None and (info.source != "window" or self._shows_vr(hwnd)):
                        found[hwnd] = info
                self.found = found
        finally:
            reader.close()

    def _shows_vr(self, hwnd):
        """A window source counts only while the window really shows a VR picture (the eyes' pictures, or a
        fisheye circle) - not a site's own 360 viewer, which shows an ordinary flat view."""
        checked, verdict = self._window_checks.get(hwnd, (-WINDOW_RECHECK_SECONDS, False))
        if time.monotonic() - checked < WINDOW_RECHECK_SECONDS:
            return verdict
        verdict = False
        capture = WindowCapture(hwnd)
        try:
            for _ in range(20):
                _, image = capture.latest()
                if image is not None:
                    break
                time.sleep(0.05)
        finally:
            capture.stop()
        if image is not None:
            gray = image[::2, ::2, 1].astype(np.float32)
            x0, y0, x1, y1 = recognize.content_box(gray)
            h, w = gray.shape
            video = gray[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
            if video.size > gray.size * 0.3:
                side, stacked = recognize.stereo_scores(video)
                verdict = max(side, stacked) >= WINDOW_SAME_PICTURE
        self._window_checks[hwnd] = (time.monotonic(), verdict)
        return verdict


class WatchButton(View):
    """For a browser window (hwnd) that shows a VR video: under it in VR, or above the dock with the video's
    title (`with_title`) when the browser is on the main screen. Its x hides it (for this video)."""

    def __init__(self, shell, hwnd, distance, with_title=False):
        super().__init__(distance=distance)
        self.shell, self.hwnd, self.with_title = shell, hwnd, with_title

    def state(self):
        info = self.shell.video_detector.found.get(self.hwnd)
        return info and (info.key, info.projection, info.stereo, self.shell.settings().private_mode)

    def build(self):
        info = self.shell.video_detector.found.get(self.hwnd)
        if info is None:
            return Row([], w=1, h=1)
        kind = (STEREO_NAMES[info.stereo] + " " if info.stereo not in ("mono", None) else "") + \
            PROJECTION_NAMES[info.projection]
        private = self.shell.settings().private_mode
        text = f"Watch in VR  ·  {kind}" + (f"  ·  {info.title}" if self.with_title and not private else "")
        h = theme.BAR - 12
        return Row([Button("watch", text, Icon.PLAY, h=h, w=1400 if self.with_title else None,
                           selected=True, on_click=lambda: self.shell.watch_video(self.hwnd)),
                    Button("dismiss", icon=Icon.CLOSE, w=int(h * 0.8), h=h, size=theme.SMALL - 6, style="danger",
                           on_click=lambda: self.shell.dismiss_video(self.hwnd))],
                   pad=8, gap=6, bg=theme.BG, radius=theme.RADIUS_PANEL, border=theme.EDGE)


class WindowPlayer:
    """A browser window's picture as the video: the browser plays it, with its sound. Play / pause go through
    Windows' media controls; there is no position to show or jump to."""
    duration, position, ended, error = 0.0, 0.0, False, None

    def __init__(self, hwnd, media):
        self.media = media
        self.latest = None                       # newest picture (BGRA)
        self.crop = (0.0, 0.0, 1.0, 1.0)         # the part of the window that holds the video (no black borders)
        self.volume = 1.0                        # (the browser's volume decides)
        self._capture = WindowCapture(hwnd)
        self._seq = -1

    @property
    def paused(self):
        playing = self.media.now_playing
        return bool(playing) and not playing["playing"]

    @property
    def buffering(self):
        return self._seq < 0

    def set_paused(self, paused):
        if paused != self.paused:
            self.media.command("play_pause")

    def seek(self, seconds):
        pass

    def frame(self):
        """The window's picture if it changed, else None."""
        seq, image = self._capture.latest()
        if image is None or seq == self._seq:
            return None
        self._seq, self.latest = seq, image
        return image

    def close(self):
        self._capture.stop()


class PhotoPlayer:
    """A picture shown around you (from Photos): one frame - or, for an animated GIF / WebP / PNG, its frames
    played over and over at their own speed."""
    duration, position, ended, error, paused, volume = 0.0, 0.0, False, None, True, 1.0
    MAX_FRAMES = 1000
    MAX_BYTES = 600 * 1024 * 1024        # animations bigger than this play only their first part

    def __init__(self, path, turns=0):
        self._image, self._sent = None, False
        self.turns = turns % 4                  # quarter turns to the right (the photo's Turn buttons)
        self._frames, self._times = [], []      # animated: BGRA frames, and when each one ends (s)
        self._start, self._shown = None, -1
        threading.Thread(target=self._load, args=(path,), daemon=True, name="photo").start()

    def _bgra(self, image):
        rgba = np.asarray(image.convert("RGBA"))
        return np.ascontiguousarray(np.rot90(rgba[..., [2, 1, 0, 3]], -self.turns))   # BGRA, like a window's

    def turn(self, step):
        """A quarter turn right (+1) or left (-1): shown turned from the next frame on."""
        self.turns = (self.turns + step) % 4
        if self._image is not None:
            self._image = np.ascontiguousarray(np.rot90(self._image, -step))
        self._frames = [np.ascontiguousarray(np.rot90(f, -step)) for f in self._frames]
        self._sent, self._shown = False, -1

    def _load(self, path):
        from PIL import Image
        try:
            with Image.open(path() if callable(path) else path) as image:   # (a safe's photo: a function)
                if getattr(image, "n_frames", 1) > 1:
                    self._load_frames(image)
                    return
                image.draft("RGB", (8192, 8192))
                self._image = self._bgra(image)
        except Exception as e:
            self.error = str(e)[:80]

    def _load_frames(self, image):
        frames, times, end, size = [], [], 0.0, 0
        for i in range(min(image.n_frames, self.MAX_FRAMES)):
            image.seek(i)
            frame = self._bgra(image)
            end += max(0.02, image.info.get("duration", 100) / 1000.0)   # browsers play 0 ms frames at ~10-20 ms
            frames.append(frame)
            times.append(end)
            size += frame.nbytes
            if i == 0:
                self._image = frame         # shown while the rest loads
            if size > self.MAX_BYTES:
                break
        self.duration, self._times = end, times
        self._frames = frames               # last: now it plays

    @property
    def animated(self):
        return len(self._frames) > 1

    @property
    def buffering(self):
        return self._image is None and self.error is None

    def frame(self):
        if self.animated:
            now = time.monotonic()
            if self._start is None:
                self._start = now
            at = (now - self._start) % self.duration
            i = min(int(np.searchsorted(self._times, at, side="right")), len(self._frames) - 1)
            if i == self._shown:
                return None
            self._shown = i
            return self._frames[i]
        if self._image is None or self._sent:
            return None
        self._sent = True
        return self._image

    def set_paused(self, paused):
        pass

    def seek(self, seconds):
        pass

    def close(self):
        self._image, self._frames = None, []


class ImmersiveVideo:
    def __init__(self, shell, info, hwnd):
        self.shell, self.info = shell, info
        self.photo = info.source == "photo"
        self.window = info.source == "window"
        self.projection = info.projection or "screen"   # unknown (a window): recognised from its picture
        self.stereo = info.stereo or "mono"
        self.fov = info.fov
        self.recognizing = self.window and (info.projection is None or info.stereo is None)
        self.front_yaw = shell.look_yaw          # the video's front is where you looked when it started
        self.look_from = [0.0, 0.0]              # 3D photo: seen from the side (parts of its distance: right, up)
        if self.photo:
            self.player = PhotoPlayer(info.opener or info.video_url, self._saved_turns(info))
        elif self.window:
            self.player = WindowPlayer(hwnd, shell.media)
        else:
            self.player = VideoPlayer(info.opener or info.video_url, info.audio_url and (info.opener or info.audio_url),
                                      info.duration, start=info.start,
                                      headers=info.headers)
            self.player.volume = shell.settings().video_volume
        self.texture = VideoTexture()
        self.depth, self.depth_job = DepthTexture(), DepthJob()   # the AI's depth: flat pictures / videos in 3D
        self._room_state = ""                    # "Make it my room": "", "making", "done", "failed"
        self.controls = Controls(self)
        self.panel = VideoSettings(self)
        self.settings_open = False
        self._pointer_seen = (None, 0.0)         # last pointer direction and when it changed
        self._closed = False
        self._unmuted = None                     # the volume before Mute
        self._next_photo = None                  # Previous / Next: the picture's VideoInfo, read in the background
        self.grab = _VideoGrab(self)             # the pointer's target for presses on nothing
        self._last_frame = None                  # what's shown now (Auto-align looks at it)
        self.align_note = ""                     # what Auto-align found
        self._load_eyes()
        if self.window:
            threading.Thread(target=self._watch_window, daemon=True, name="vr-video-window").start()

    def _watch_window(self):
        """Window source, once a second: cut off black borders (a letterboxed video); the first seconds also
        recognise the layout if the page didn't tell."""
        scores = []
        while not self._closed:
            image = self.player.latest
            if image is not None:
                gray = image[::2, ::2, 1].astype(np.float32)        # green is close enough to brightness
                x0, y0, x1, y1 = recognize.content_box(gray)
                self.player.crop = (x0, y0, x1, y1)
                h, w = gray.shape
                video = gray[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
                if self.recognizing and video.size:
                    scores.append(recognize.stereo_scores(video))
                    if len(scores) >= WINDOW_LOOKS:
                        stereo = self.info.stereo or recognize.stereo_of(np.mean(scores, axis=0))
                        picture = recognize.from_picture(video, stereo)
                        layout = recognize.Layout(self.info.projection, self.info.stereo, self.info.fov).merged(picture)
                        self.stereo = layout.stereo or "mono"
                        self.projection = layout.projection or ("equi180" if self.stereo != "mono" else "screen")
                        self.fov = layout.fov
                        self.recognizing = False
            time.sleep(1.0)

    def close(self):
        self._closed = True
        if not self.photo and not self.window:
            self.shell.remember_position(self.info, self.projection, self.stereo, self.player.position)
        self.player.close()
        self.texture.delete()
        self.depth.delete()
        self.controls.release()
        self.panel.release()

    def recentered(self):
        self.front_yaw = 0.0

    def recenter_here(self):
        """The video's middle goes where you look - left / right and up / down."""
        self.front_yaw = self.shell.look_yaw
        self.look_from = [0.0, 0.0]
        self.set_value("video_tilt", self.shell.look_pitch)

    def straighten(self):
        self.set_value("video_tilt", 0.0)

    # -- actions from the controls -------------------------------------------------------------------
    def play_pause(self):
        if self.player.ended:
            self.player.seek(0)
            self.player.set_paused(False)
        else:
            self.player.set_paused(not self.player.paused)

    def skip(self, seconds):
        self.player.seek(self.player.position + seconds)

    def seek_fraction(self, fraction):
        if self.info.duration:
            self.player.seek(fraction * self.info.duration)

    def set_projection(self, projection):
        if self.projection != "mesh":     # the cube layout is only ever 360°
            self.projection = projection
            self.recognizing = False      # your choice wins

    def set_stereo(self, stereo):
        self.stereo = stereo
        self.recognizing = False

    def toggle_settings(self):
        self.settings_open = not self.settings_open

    # -- the AI's depth -------------------------------------------------------------------------------
    def flat(self):
        """A flat 2D picture / video (only those can be made 3D)."""
        return self.projection == "screen" and self.stereo == "mono" and not self.window

    def photo_depth_on(self, s):
        return self.photo and self.flat() and s.photo_depth != "off"

    def ai_depth_on(self, s):
        return not self.photo and self.flat() and s.video_ai_depth

    def set_photo_depth(self, mode):
        self.set_value("photo_depth", mode)

    def toggle_living(self):
        s = self.shell.settings()
        self.set_value("photo_living", not s.photo_living)
        if s.photo_depth == "off":
            self.set_value("photo_depth", "3d")    # living needs depth

    def make_room(self):
        """This photo becomes the surroundings (Settings > Surroundings keeps it)."""
        if self._room_state == "making":
            return
        self._room_state = "making"
        path, projection = self.info.video_url, self.projection

        def work():
            from ..video import room_maker
            try:
                name = room_maker.make(path, projection)
                self.shell.store.update(environment=name, environment_turn=0.0)
                self._room_state = "done"
                self.shell.toast("Your photo is now the room around you (Settings > Surroundings)")
            except Exception:
                from ..core.log import log_error
                log_error("make room")
                self._room_state = "failed"

        threading.Thread(target=work, daemon=True, name="make-room").start()

    def depth_status(self):
        state = self.depth_job.state
        return {"downloading": "AI: getting the depth model...", "working": "AI: seeing the depth...",
                "failed": "AI depth didn't work (see psvr-screen.log)"}.get(state, "")

    def toggle_mute(self):
        volume = self.shell.settings().video_volume
        if volume > 0:
            self._unmuted = volume
            self.set_value("video_volume", 0.0)
        else:
            self.set_value("video_volume", self._unmuted or VIDEO_DEFAULTS["video_volume"])

    def album_place(self):
        """(index, count) of the picture in the pictures Previous / Next go through, or None."""
        album = self.info.album
        if not self.photo or len(album) < 2 or self.info.video_url not in album:
            return None
        return album.index(self.info.video_url), len(album)

    @staticmethod
    def _turn_key(info):
        return files.turn_key(info.video_url)

    def _saved_turns(self, info):
        return self.shell.settings().photo_turns.get(self._turn_key(info), 0)

    def turn_photo(self, step):
        """Turn the photo a quarter right (+1) or left (-1); remembered for it (not in private mode, not a
        photo from a locked room's safe)."""
        self.player.turn(step)
        self.depth_job.reset()                 # (its depth is worked out again, turned)
        self.depth.size = None
        s = self.shell.settings()
        if not s.private_mode and self.info.opener is None:
            turns = dict(s.photo_turns)
            key = self._turn_key(self.info)
            if self.player.turns:
                turns[key] = self.player.turns
            else:
                turns.pop(key, None)
            self.shell.store.update(photo_turns=turns)

    def step_photo(self, step):
        """Previous / next picture, read in the background and shown where this one is."""
        place = self.album_place()
        if place is None:
            return
        album = self.info.album
        path = album[(place[0] + step) % len(album)]

        def read():
            try:
                info = files.probe_photo(Path(path))
            except Exception:
                return
            info.album = album
            self._next_photo = info

        threading.Thread(target=read, daemon=True, name="photo-step").start()

    def set_value(self, key, value):
        """A video setting (saved: the next video starts with it too - except the eyes' settings, which each
        3D video keeps for itself)."""
        self.shell.store.update(**{key: clamp(key, value) if key in LIMITS else value})
        if key == "video_volume":
            self.player.volume = value
        if key in EYE_KEYS:
            self._save_eyes()

    # -- each video's own eye settings ---------------------------------------------------------------
    def _eye_key(self):
        return files.turn_key(self.info.key)     # (scrambled: settings.json doesn't list what you watched)

    def _load_eyes(self):
        """This video's eye settings (lined up before), or all at zero for one not tuned yet."""
        saved = self.shell.settings().eye_tuning.get(self._eye_key(), {})
        self.shell.store.update(**{k: float(saved.get(k, 0.0)) for k in EYE_KEYS})

    def _save_eyes(self):
        s = self.shell.settings()
        if s.private_mode or self.info.opener is not None:     # (private / from a locked room's safe: not kept)
            return
        tuning = dict(s.eye_tuning)
        values = {k: getattr(s, k) for k in EYE_KEYS if abs(getattr(s, k)) > 1e-6}
        if values:
            tuning[self._eye_key()] = values
        else:
            tuning.pop(self._eye_key(), None)
        self.shell.store.update(eye_tuning=tuning)

    def auto_align(self):
        """Line the eyes up by themselves: compares the two eyes' pictures of what's shown now (matching
        details) and sets Eye height, Eye turn and Eye size so they lie on top of each other. IPD (how deep)
        is left as it is - that's taste."""
        frame, stereo = self._last_frame, self.stereo
        crop = getattr(self.player, "crop", (0.0, 0.0, 1.0, 1.0))
        if frame is None or stereo == "mono":
            self.align_note = "Auto-align is for 3D videos (side by side / top and bottom)"
            return
        self.align_note = "Lining up the eyes..."

        def work():
            try:
                result = measure_eyes(frame, stereo, crop)
                dy_degrees = result[0] * self._degrees_per_px(result[3])
                self.shell.later(lambda: self._aligned(dy_degrees, result[1], result[2], result[4]))
            except ValueError as e:
                self.align_note = f"Couldn't line up: {e} - try a moment with more detail"
            except Exception:
                from ..core.log import log_error
                log_error("auto-align eyes")
                self.align_note = "Auto-align didn't work (see psvr-screen.log)"

        threading.Thread(target=work, daemon=True, name="eye-align").start()

    def _degrees_per_px(self, eye_width):
        s = self.shell.settings()
        across = {"equi": 360.0, "equi180": 180.0, "fisheye": self.fov or 190.0, "mesh": 90.0 * 3,
                  "screen": s.video_screen_degrees}.get(self.projection, 180.0)
        return across / max(eye_width, 1)

    def _aligned(self, dy_degrees, turn, scale, found):
        """Auto-align's answer: what the right eye's picture is off by, against the left one's - undone here."""
        s = self.shell.settings()
        self.set_value("video_eye_height", s.video_eye_height + ALIGN_SIGNS[0] * dy_degrees)
        self.set_value("video_eye_roll", s.video_eye_roll + ALIGN_SIGNS[1] * turn)
        self.set_value("video_eye_scale", s.video_eye_scale + ALIGN_SIGNS[2] * (scale - 1.0))
        self.align_note = (f"Lined up ({found} matching details): height {dy_degrees:+.2f}°, turn {turn:+.2f}°, "
                           f"size {100 * (scale - 1):+.1f}%")

    def place(self, right, forward):
        """Move yourself to this spot of the dome map (parts of the radius)."""
        length = math.hypot(right, forward)
        if length > PLACE_LIMIT:   # stay inside the dome
            right, forward = right * PLACE_LIMIT / length, forward * PLACE_LIMIT / length
        self.shell.store.update(video_place_right=right, video_place_forward=forward)

    def center_place(self):
        self.shell.store.update(video_place_right=0.0, video_place_forward=0.0, video_place_up=0.0)

    def reset_settings(self):
        self.shell.store.update(**VIDEO_DEFAULTS)
        self.player.volume = VIDEO_DEFAULTS["video_volume"]
        self._save_eyes()                      # (this video's eyes back to zero too)

    # -- every frame ---------------------------------------------------------------------------------
    def surfaces(self, now):
        if self._next_photo is not None:   # the next picture is read: show it (same place around you)
            info, self._next_photo = self._next_photo, None
            self.player.close()
            self.info, self.player = info, PhotoPlayer(info.opener or info.video_url, self._saved_turns(info))
            self.projection, self.stereo, self.fov = info.projection or "screen", info.stereo or "mono", info.fov
            self.depth_job.reset()
            self.depth.size = None
            self._room_state = ""
            self._load_eyes()
        s = self.shell.settings()
        frame = self.player.frame()
        if frame is not None:
            self._last_frame = frame
            if self.window or self.photo:
                self.texture.upload_bgra(frame)
            else:
                self.texture.upload(frame)
                if self.ai_depth_on(s):
                    self.depth_job.video_frame(frame)
        if self.photo and self.photo_depth_on(s) and self.player._image is not None:
            self.depth_job.photo(self.info.video_url, self.player._image,
                                 s.private_mode or self.info.opener is not None, self.player.turns)   # (a safe's photo: nothing on disk)
        made = self.depth_job.result()
        if made is not None:
            self.depth.upload(made)
        depth_shown = (self.photo and self.photo_depth_on(s)) or (not self.photo and self.ai_depth_on(s))
        projection = self.projection
        if self.photo and s.photo_depth == "inside" and self.photo_depth_on(s):
            projection = "inside"
        front = geometry.rotate_y(-self.front_yaw)
        turned = front @ geometry.rotate_x(s.video_tilt) @ geometry.rotate_z(-s.video_roll)
        out = [Surface("video", turned, 1, 1, texture=self.texture, shape="sphere",   # centered between your eyes
                       data={"projection": projection, "stereo": self.stereo, "distance": s.video_distance,
                             "screen_degrees": s.video_screen_degrees, "screen_curve": s.video_screen_curve,
                             "size": s.video_size, "brightness": s.video_brightness,
                             "contrast": s.video_contrast, "saturation": s.video_saturation,
                             "ipd": s.video_ipd if self.stereo != "mono" or depth_shown else 0.0,
                             "eye_height": s.video_eye_height if self.stereo != "mono" or depth_shown else 0.0,
                             "eye_roll": s.video_eye_roll if self.stereo != "mono" else 0.0,
                             "eye_scale": s.video_eye_scale if self.stereo != "mono" else 0.0,
                             "move_away": s.video_move_away,
                             "place": (s.video_place_right, s.video_place_up, s.video_place_forward),
                             "fov": self.fov, "crop": getattr(self.player, "crop", (0.0, 0.0, 1.0, 1.0)),
                             "depth": self.depth if depth_shown else None,
                             "depth_amount": s.photo_depth_amount, "time": now, "ipd_m": s.ipd,
                             "living": self.photo and s.photo_living and depth_shown,
                             "look_from": tuple(c * s.video_distance for c in self.look_from)   # (m)
                             if depth_shown else (0.0, 0.0)})]
        if self._controls_visible(now):
            self.controls.refresh()
            model = front @ geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ geometry.tilt(DOCK_TILT)
            out.append(Surface("video-controls", model, self.controls.width, self.controls.height,
                               texture=self.controls.texture, layer=INTERFACE, target=self.controls,
                               data={"ui": True}))
            if self.settings_open:   # just above the controls, over their right side (where ⚙ is)
                self.panel.refresh()
                x = self.controls.width / 2 - self.panel.width / 2
                y = self.controls.height / 2 + PANEL_GAP + self.panel.height / 2
                out.append(Surface("video-settings", model @ geometry.translate(x, y, 0.01), self.panel.width,
                                   self.panel.height, texture=self.panel.texture, layer=INTERFACE,
                                   target=self.panel, data={"ui": True}))
        return out

    def _controls_visible(self, now):
        pointer = self.shell.pointer
        if not pointer.active:
            return False
        direction = (round(pointer.yaw, 3), round(pointer.pitch, 3))
        if direction != self._pointer_seen[0]:
            self._pointer_seen = (direction, now)
        on_ui = pointer.hit is not None and pointer.hit[0].id in ("video-controls", "video-settings")
        return (self.player.paused or self.player.ended or self.settings_open or on_ui
                or now - self._pointer_seen[1] < CONTROLS_SECONDS)


LOOK_AROUND_PER_DEGREE = 0.006    # 3D photo: your eyes seem to move this part of its distance per degree dragged
LOOK_AROUND_MAX = 0.15            # ...at most this part of the distance to a side (farther shows the gaps behind)

class _VideoGrab:
    """Left button on empty space + drag: the video follows the pointer (turns left / right, tilts up / down).
    A 3D photo (made 3D by the AI, on a screen): drag to look at it from the side - as if you stepped there."""

    def __init__(self, video):
        self.video = video
        self._start = None

    def on_pointer(self, e):
        v = self.video
        s = v.shell.settings()
        look_around = v.photo and v.projection == "screen" and v.photo_depth_on(s) and s.photo_depth == "3d"
        if e.kind == "press" and e.button == "left":
            self._start = tuple(v.look_from) if look_around else (v.front_yaw, s.video_tilt)
        elif e.kind == "drag" and self._start is not None:
            if look_around:
                x0, y0 = self._start
                step = LOOK_AROUND_PER_DEGREE
                v.look_from = [max(-LOOK_AROUND_MAX, min(LOOK_AROUND_MAX, x0 - (e.yaw - e.start_yaw) * step)),
                               max(-LOOK_AROUND_MAX, min(LOOK_AROUND_MAX, y0 - (e.pitch - e.start_pitch) * step))]
                return
            yaw0, tilt0 = self._start
            v.front_yaw = yaw0 + (e.yaw - e.start_yaw)
            v.set_value("video_tilt", tilt0 + (e.pitch - e.start_pitch))
        elif e.kind == "release":
            self._start = None


def _time(seconds):
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Controls(View):
    """  0:41 ━━━━━━━━━━━●──────────────── 7:52
         Title · status          ⟲10  (▶)  10⟳        ⚙  ⟳  🖱 PC  ✕ Exit   """

    def __init__(self, video):
        super().__init__(distance=DOCK_DISTANCE)
        self.video = video

    def state(self):
        v, p = self.video, self.video.player
        return (int(p.position), p.paused, p.ended, p.buffering, p.error, v.settings_open, v.recognizing,
                v.projection, v.stereo, v.shell.settings().private_mode, self.ctx.drag_start,
                v.shell.settings().video_volume, v.info.video_url if v.photo else None,
                v.shell.settings().photo_depth, v.shell.settings().photo_living, v._room_state, v.depth_status(),
                v.shell.settings().voice_control, v.shell.voice is not None and v.shell.voice.listening,
                tuple(round(c, 3) for c in v.look_from))

    def build(self):
        v, p = self.video, self.video.player
        duration = v.info.duration
        position = p.position
        h = theme.BAR - 8
        if p.error:
            status, color = "Can't play: " + p.error, theme.DANGER
        elif p.buffering:
            status, color = "Loading...", theme.ACCENT_LIGHT
        elif v.recognizing:
            status, color = "Recognising the video...", theme.ACCENT_LIGHT
        else:
            status, color = PROJECTION_NAMES[v.projection] + ("  ·  3D" if v.stereo != "mono" else ""), theme.MUTED
        playing = not p.paused and not p.ended

        def button(key, text=None, icon=None, action=None, style="flat", selected=False, w=None):
            return Button(key, text, icon, h=h, w=w, style=style, selected=selected, size=theme.TEXT - 4,
                          on_click=action)

        if v.photo:
            progress = self._photo_row()
            s = v.shell.settings()
            if v.photo_depth_on(s) and s.photo_depth == "3d":   # 3D: where you look at it from
                progress = Column([progress, self._look_row()], gap=8)
        elif v.window:   # the browser plays it: jump around there
            progress = Label("From the browser window - put the video in full screen there for the best picture",
                             size=theme.SMALL - 4, color=theme.MUTED, w=CONTROLS_WIDTH, h=theme.ROW, align="center")
        else:
            progress = Row([Label(_time(position), size=theme.SMALL, color=theme.FG, w=150, h=theme.ROW,
                                  align="right"),
                            SeekBar("seek", position / duration if duration else 0.0, v.seek_fraction, h=theme.ROW,
                                    flex=1),
                            Label(_time(duration) if duration else "live", size=theme.SMALL, color=theme.MUTED,
                                  w=150, h=theme.ROW),
                            *self._volume()],
                           w=CONTROLS_WIDTH, h=theme.ROW, gap=8)
        title = shown_title(v.info.title, private=v.shell.settings().private_mode)
        about = Column([Label(title, size=theme.SMALL, h=int(theme.ROW * 0.8), w=SIDE_WIDTH, indent=16),
                        Label(status, size=theme.SMALL - 4, color=color, h=int(theme.ROW * 0.7), w=SIDE_WIDTH,
                              indent=16)], w=SIDE_WIDTH, h=h)
        play = Button("play", icon=Icon.PAUSE if playing else Icon.PLAY, w=h + 14, h=h + 14, style="primary",
                      size=theme.TEXT, on_click=v.play_pause)
        album = v.album_place()
        if album is not None:   # a picture from Photos: previous / next of its folder (or photo wall)
            center = Row([button("previous", icon=Icon.PREVIOUS, action=lambda: v.step_photo(-1), w=h + 30),
                          Label(f"{album[0] + 1} / {album[1]}", size=theme.TEXT - 4, w=190, h=h, align="center"),
                          button("next", icon=Icon.NEXT, action=lambda: v.step_photo(1), w=h + 30)], gap=6, h=h + 14)
        elif v.photo:
            center = Spacer(w=h)
        elif v.window:
            center = Row([play], h=h + 14)
        else:
            center = Row([button("back", f"−{SKIP_SECONDS}s", action=lambda: v.skip(-SKIP_SECONDS), w=150), play,
                          button("forward", f"+{SKIP_SECONDS}s", action=lambda: v.skip(SKIP_SECONDS), w=150)],
                         gap=18, h=h + 14)
        listening = v.shell.voice is not None and v.shell.voice.listening
        talk = ([button("talk", icon=Icon.MIC, action=v.shell.listen, selected=listening, w=h + 10)]
                if v.shell.settings().voice_control else [])
        right = Row([Spacer(flex=1), *talk,
                     button("settings", icon=Icon.SETTINGS, action=v.toggle_settings, selected=v.settings_open,
                            w=h + 10),
                     button("recenter", icon=Icon.RECENTER, action=v.recenter_here, w=h + 10),
                     button("mouse", "PC", Icon.MOUSE, action=lambda: v.shell.dock_action("mouse_to_pc")),
                     button("exit", "Exit", Icon.CLOSE, action=v.shell.stop_video, style="danger")],
                    w=SIDE_WIDTH, h=h, gap=6)
        buttons = Row([about, Spacer(flex=1), center, Spacer(flex=1), right], w=CONTROLS_WIDTH, h=h + 14)
        return Panel([progress, buttons], pad=20, gap=6)

    def _photo_row(self):
        """A photo: make it 3D, step inside it, let it live, make it the room."""
        v, s = self.video, self.video.shell.settings()
        small = theme.ROW

        def chip(key, text, icon, action, selected=False):
            return Button(key, text, icon, h=small, style="flat", selected=selected, size=theme.TEXT - 8,
                          on_click=action)

        room = {"making": "Making the room...", "done": "It's your room now"}.get(v._room_state, "Make it my room")
        items = []
        if v.flat():
            items += [chip("depth:off", "Flat", Icon.PHOTO, lambda: v.set_photo_depth("off"), s.photo_depth == "off"),
                      chip("depth:3d", "3D", Icon.SPHERE, lambda: v.set_photo_depth("3d"), s.photo_depth == "3d"),
                      chip("depth:inside", "Step inside", Icon.EYE, lambda: v.set_photo_depth("inside"),
                           s.photo_depth == "inside"),
                      chip("living", "Living", Icon.STAR, v.toggle_living, s.photo_living and s.photo_depth != "off"),
                      Spacer(w=12)]
        status = v.depth_status() if v.flat() and s.photo_depth != "off" else ""
        if v.flat():
            items += [chip("turn:left", "Turn left", None, lambda: v.turn_photo(-1)),
                      chip("turn:right", "Turn right", None, lambda: v.turn_photo(1)), Spacer(w=12)]
        items += [chip("room", room, Icon.HOME, v.make_room, v._room_state == "done"),
                  Label(status or ("Drag empty space to look around" if not v.flat() else ""), size=theme.SMALL - 8,
                        color=theme.ACCENT_LIGHT if status else theme.MUTED, h=small, flex=1, indent=12)]
        return Row(items, w=CONTROLS_WIDTH, h=small, gap=6)

    def _look_row(self):
        """3D photo: the Look-from pad (press where you want to stand - the middle is straight in front) and Center."""
        v = self.video

        def pick(right, up):
            v.look_from = [right * LOOK_AROUND_MAX, up * LOOK_AROUND_MAX]

        x, y = (c / LOOK_AROUND_MAX for c in v.look_from)
        return Row([Label("Look from", size=theme.SMALL - 4, w=220, h=LOOK_PAD_H, indent=16),
                    LookPad("look-pad", x, y, pick),
                    Button("look-center", "Straight on", Icon.RECENTER, h=theme.ROW - 8, style="normal",
                           size=theme.TEXT - 8, on_click=lambda: pick(0.0, 0.0)),
                    Label("Or drag the photo", size=theme.SMALL - 8, color=theme.MUTED, h=LOOK_PAD_H, flex=1,
                          indent=12)], w=CONTROLS_WIDTH, h=LOOK_PAD_H, gap=12)

    def _volume(self):
        """🔊 (mute / unmute) and a slider - 100% = as recorded, up to 150%."""
        v = self.video
        volume = v.shell.settings().video_volume
        lo, hi = LIMITS["video_volume"]
        return [Button("mute", icon=Icon.MUTE if volume <= 0 else Icon.VOLUME, w=theme.ROW, h=theme.ROW, style="flat",
                       size=theme.TEXT - 6, on_click=v.toggle_mute),
                Slider("volume", "", volume * 100, lo * 100, hi * 100, "{:.0f}%", w=VOLUME_WIDTH, h=theme.ROW,
                       on_change=lambda value: v.set_value("video_volume", value / 100))]


class VideoSettings(View):
    """⚙ of the video controls, in tabs like the VR players have them: Position (where the video is),
    Size & 3D (what it is and how big / deep it looks), Picture. Everything except View and 3D is remembered."""
    TABS = (("position", "Position"), ("size", "Size & 3D"), ("picture", "Picture"))

    def __init__(self, video):
        super().__init__(distance=DOCK_DISTANCE)
        self.video = video
        self.tab = "position"

    def state(self):
        v, s = self.video, self.video.shell.settings()
        return ((v.projection, v.stereo, v.recognizing, self.tab, s.photo_depth_amount, s.photo_depth, v.depth_status())
                + tuple(getattr(s, k) for k in VIDEO_DEFAULTS))

    def _show(self, tab):
        self.tab = tab

    def build(self):
        v, s = self.video, self.video.shell.settings()
        w = PANEL_WIDTH

        def choices(label, options, current, pick, disabled=()):
            buttons = [Button(f"{label}:{value}", name, h=theme.ROW - 8, flex=1, size=theme.TEXT - 6,
                              selected=value == current, muted=value in disabled,
                              on_click=(lambda value=value: pick(value)) if value not in disabled else None)
                       for value, name in options]
            return Row([Label(label, w=int(w * 0.2), indent=24), Row(buttons, gap=6, flex=1, h=theme.ROW - 8)],
                       w=w, h=theme.ROW, gap=8)

        def slider(key, label, fmt, scale=1.0, width=None):
            lo, hi = LIMITS[key]
            return Slider(key, label, getattr(s, key) * scale, lo * scale, hi * scale, fmt, w=width or w,
                          on_change=lambda value: v.set_value(key, value / scale))

        def distance_slider():
            """Logarithmic: as many steps from 0.3 to 1 m as from 30 to 100 m."""
            lo, hi = LIMITS["video_distance"]
            return Slider("video_distance", "Distance", math.log(s.video_distance), math.log(lo), math.log(hi),
                          w=w, text=lambda value: f"{math.exp(value):.2f} m" if value < math.log(10)
                          else f"{math.exp(value):.0f} m",
                          on_change=lambda value: v.set_value("video_distance", math.exp(value)))

        def hint(text):
            return Label(text, size=theme.SMALL - 6, color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24)

        tabs = [Button("tab:" + key, name, h=theme.ROW - 8, size=theme.TEXT - 6, style="flat", selected=self.tab == key,
                       on_click=lambda key=key: self._show(key)) for key, name in self.TABS]
        rows = [Row([Label("Video", size=theme.TITLE, color=theme.ACCENT, indent=24), Spacer(flex=1), *tabs,
                     Button("reset", "Reset", h=theme.ROW - 8, style="flat", size=theme.TEXT - 6,
                            on_click=v.reset_settings)], w=w, h=int(theme.ROW * 1.2), gap=6)]

        if self.tab == "size":
            views = [("screen", "Screen"), ("equi180", "180°"), ("equi", "360°"), ("fisheye", "Fisheye")]
            current_view = "equi" if v.projection == "mesh" else v.projection
            cube_only = ("screen", "equi180", "fisheye") if v.projection == "mesh" else ()   # the cube: only 360°
            rows += [choices("View", views, current_view, v.set_projection, disabled=cube_only),
                     choices("3D", [("mono", "Off"), ("side_by_side", "Side by side"), ("top_bottom", "Top / bottom")],
                             v.stereo, v.set_stereo)]
            if v.projection == "screen":
                rows += [slider("video_screen_degrees", "Screen size", "{:.0f}°"),
                         slider("video_screen_curve", "Curve", "{:.0f}%", 100)]
            else:
                rows += [slider("video_size", "Size (zoom)", "{:.2f}x")]
            eye_rows = [slider("video_ipd", "IPD (depth)", "{:+.2f}°"),
                        hint("IPD = how far the 3D feels:  ←  closer, bigger      farther, smaller  →"),
                        slider("video_eye_height", "Eye height", "{:+.2f}°"),
                        slider("video_eye_roll", "Eye turn", "{:+.2f}°"),
                        slider("video_eye_scale", "Eye size", "{:+.1f}%", 100),
                        Row([Spacer(w=24), Button("auto-align", "Auto-align eyes", Icon.EYE, h=theme.ROW - 10,
                                                  style="primary", size=theme.TEXT - 8, on_click=v.auto_align),
                             Label(v.align_note or "Lines the eyes up by itself. Saved for this video.",
                                   size=theme.SMALL - 8, color=theme.MUTED,
                                   h=theme.ROW - 10, flex=1, indent=12)], w=w, h=theme.ROW - 6, gap=6)]
            if v.stereo != "mono":
                rows += eye_rows
            elif v.flat() and v.photo:
                rows += [choices("AI 3D", [("off", "Flat"), ("3d", "3D"), ("inside", "Step inside")],
                                 s.photo_depth, v.set_photo_depth)]
                if s.photo_depth != "off":
                    rows += [slider("photo_depth_amount", "3D depth", "{:.1f}x")] + eye_rows
            elif v.flat():
                rows += [Toggle("ai-depth", "AI 3D: make this flat video 3D (experimental)", s.video_ai_depth, w=w,
                                on_change=lambda value: v.set_value("video_ai_depth", value)),
                         slider("photo_depth_amount", "3D depth", "{:.1f}x")]
                rows += eye_rows if s.video_ai_depth else []
                rows.append(hint(v.depth_status() or "The AI sees the depth of every frame, on this PC - nothing is uploaded"))
            else:
                rows.append(hint("Eye separation is for 3D videos (3D above)"))
            return Panel(rows, pad=20, gap=4)

        if self.tab == "picture":
            rows += [slider("video_brightness", "Brightness", "{:.0f}%", 100),
                     slider("video_contrast", "Contrast", "{:.0f}%", 100),
                     slider("video_saturation", "Colors", "{:.0f}%", 100),
                     slider("video_volume", "Volume", "{:.0f}%", 100)]
            return Panel(rows, pad=20, gap=4)

        def move_away_slider():
            """Logarithmic too (from 0): fine steps close by, big ones far away."""
            hi = math.log1p(LIMITS["video_move_away"][1])
            return Slider("video_move_away", "Move away", math.log1p(s.video_move_away), 0.0, hi, w=w,
                          text=lambda value: f"{math.expm1(value):.2f} m" if value < math.log1p(10)
                          else f"{math.expm1(value):.0f} m",
                          on_change=lambda value: v.set_value("video_move_away", math.expm1(value)))

        place = Row([DomeMap("dome-map", s.video_place_right, s.video_place_forward, v.place),
                     Column([Label("Your place in the video", w=w - DOME_MAP_PX - 40, indent=24),
                             Label("Press the map to move there (top = front)", size=theme.SMALL - 6,
                                   color=theme.MUTED, w=w - DOME_MAP_PX - 40, h=int(theme.ROW * 0.6), indent=24),
                             slider("video_place_up", "Height", "{:+.0%}", width=w - DOME_MAP_PX - 40),
                             Row([Spacer(flex=1), Button("center", "Center", Icon.RECENTER, h=theme.ROW - 8,
                                                         size=theme.TEXT - 6, on_click=v.center_place)],
                                 w=w - DOME_MAP_PX - 40, h=theme.ROW)],
                            w=w - DOME_MAP_PX - 20)],
                    w=w, h=DOME_MAP_PX + 16, gap=8)
        rows += [place,
                 move_away_slider(),
                 Label("Move away: the screens around you go farther back - and look smaller", size=theme.SMALL - 6,
                       color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24),
                 distance_slider(),
                 Label("Distance: deeper or nearer for your eyes, same size", size=theme.SMALL - 6,
                       color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24)]
        rows += [slider("video_tilt", "Tilt (up / down)", "{:+.0f}°"),
                 slider("video_roll", "Roll", "{:+.0f}°"),
                 hint("Roll fixes a crooked horizon  ·  or grab empty space and drag the video")]
        return Panel(rows, pad=20, gap=4)


class LookPad(Widget):
    """Where you look at a 3D photo from: a small pad, the middle = straight in front, the edges = as far to that
    side as it goes. Press or drag on it: `on_pick(right, up)`, -1..1."""
    interactive = True

    def __init__(self, key, right, up, on_pick):
        super().__init__(LOOK_PAD_W, LOOK_PAD_H, key)
        self.right, self.up, self.on_pick = right, up, on_pick

    def draw(self, d, ctx):
        x0, y0, x1, y1 = (int(v) for v in self.box)
        hovered = ctx.hovered(self.key) or ctx.pressed(self.key)
        d.rounded_rectangle((x0, y0, x1, y1), 18, fill=theme.BUTTON, outline=theme.ACCENT if hovered else theme.TRACK,
                            width=3)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        d.line((x0 + 16, cy, x1 - 16, cy), fill=theme.TRACK, width=2)
        d.line((cx, y0 + 12, cx, y1 - 12), fill=theme.TRACK, width=2)
        px = cx + self.right * (x1 - x0 - 40) / 2
        py = cy - self.up * (y1 - y0 - 40) / 2
        d.ellipse((px - 16, py - 16, px + 16, py + 16), fill=theme.FG, outline=theme.ACCENT, width=5)

    def _pick(self, ctx, e):
        if e.u is None or not self.on_pick:
            return
        x0, y0, x1, y1 = self.box
        x, y = e.u * ctx.size[0], e.v * ctx.size[1]
        right = max(-1.0, min(1.0, (x - (x0 + x1) / 2) / ((x1 - x0 - 40) / 2)))
        up = max(-1.0, min(1.0, ((y0 + y1) / 2 - y) / ((y1 - y0 - 40) / 2)))
        self.on_pick(right, up)

    def press(self, ctx, e):
        self._pick(ctx, e)

    def drag(self, ctx, e):
        self._pick(ctx, e)


class DomeMap(Widget):
    """The dome seen from above, its front at the top, with a dot where you are. Press or drag on it to move
    there: `on_pick(right, forward)` in parts of the dome's radius."""
    interactive = True

    def __init__(self, key, right, forward, on_pick):
        super().__init__(DOME_MAP_PX, DOME_MAP_PX, key)
        self.right, self.forward, self.on_pick = right, forward, on_pick

    def _circle(self):
        x0, y0, x1, y1 = self.box
        r = min(x1 - x0, y1 - y0) / 2 - 12
        return (x0 + x1) / 2, (y0 + y1) / 2, r

    def draw(self, d, ctx):
        cx, cy, r = self._circle()
        hovered = ctx.hovered(self.key) or ctx.pressed(self.key)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=theme.BUTTON, outline=theme.ACCENT if hovered else theme.TRACK,
                  width=4)
        d.arc((cx - r, cy - r, cx + r, cy + r), 225, 315, fill=theme.ACCENT, width=12)   # the front
        d.text((cx, cy - r + 34), "front", font=theme.font(theme.SMALL - 8), fill=theme.ACCENT_LIGHT, anchor="mm")
        d.line((cx - 10, cy, cx + 10, cy), fill=theme.MUTED, width=2)
        d.line((cx, cy - 10, cx, cy + 10), fill=theme.MUTED, width=2)
        px, py = cx + self.right * r, cy - self.forward * r
        d.ellipse((px - 20, py - 20, px + 20, py + 20), fill=theme.FG, outline=theme.ACCENT, width=5)

    def _pick(self, ctx, e):
        if e.u is None or not self.on_pick:
            return
        cx, cy, r = self._circle()
        x, y = e.u * ctx.size[0], e.v * ctx.size[1]
        self.on_pick((x - cx) / r, (cy - y) / r)

    def press(self, ctx, e):
        self._pick(ctx, e)

    def drag(self, ctx, e):
        self._pick(ctx, e)
