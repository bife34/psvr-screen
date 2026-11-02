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

import numpy as np

from ..core import geometry
from ..core.settings import LIMITS, Settings, clamp, shown_title
from ..render.video_sphere import VideoTexture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Label, Row, SeekBar, Slider, Spacer, Widget
from ..video import recognize, sites
from ..video.player import VideoPlayer
from ..win.capture import WindowCapture
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
PLACE_LIMIT = 0.9                  # you can go this far from the middle (part of the radius)


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
                   pad=6, gap=6, bg=theme.BG, radius=theme.RADIUS)


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


class ImmersiveVideo:
    def __init__(self, shell, info, hwnd):
        self.shell, self.info = shell, info
        self.window = info.source == "window"
        self.projection = info.projection or "screen"   # unknown (a window): recognised from its picture
        self.stereo = info.stereo or "mono"
        self.fov = info.fov
        self.recognizing = self.window and (info.projection is None or info.stereo is None)
        self.front_yaw = shell.look_yaw          # the video's front is where you looked when it started
        if self.window:
            self.player = WindowPlayer(hwnd, shell.media)
        else:
            self.player = VideoPlayer(info.video_url, info.audio_url, info.duration, headers=info.headers)
            self.player.volume = shell.settings().video_volume
        self.texture = VideoTexture()
        self.controls = Controls(self)
        self.panel = VideoSettings(self)
        self.settings_open = False
        self._pointer_seen = (None, 0.0)         # last pointer direction and when it changed
        self._closed = False
        self.grab = _VideoGrab(self)             # the pointer's target for presses on nothing
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
        self.player.close()
        self.texture.delete()
        self.controls.release()
        self.panel.release()

    def recentered(self):
        self.front_yaw = 0.0

    def recenter_here(self):
        """The video's middle goes where you look - left / right and up / down."""
        self.front_yaw = self.shell.look_yaw
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

    def set_value(self, key, value):
        """A video setting (saved: the next video starts with it too)."""
        self.shell.store.update(**{key: clamp(key, value) if key in LIMITS else value})
        if key == "video_volume":
            self.player.volume = value

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

    # -- every frame ---------------------------------------------------------------------------------
    def surfaces(self, now):
        frame = self.player.frame()
        if frame is not None:
            if self.window:
                self.texture.upload_bgra(frame)
            else:
                self.texture.upload(frame)
        s = self.shell.settings()
        front = geometry.rotate_y(-self.front_yaw)
        turned = front @ geometry.rotate_x(s.video_tilt) @ geometry.rotate_z(-s.video_roll)
        out = [Surface("video", turned, 1, 1, texture=self.texture, shape="sphere",   # centered between your eyes
                       data={"projection": self.projection, "stereo": self.stereo, "distance": s.video_distance,
                             "screen_degrees": s.video_screen_degrees, "screen_curve": s.video_screen_curve,
                             "size": s.video_size, "brightness": s.video_brightness,
                             "contrast": s.video_contrast, "saturation": s.video_saturation,
                             "ipd": s.video_ipd if self.stereo != "mono" else 0.0,
                             "move_away": s.video_move_away,
                             "place": (s.video_place_right, s.video_place_up, s.video_place_forward),
                             "fov": self.fov, "crop": getattr(self.player, "crop", (0.0, 0.0, 1.0, 1.0))})]
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


class _VideoGrab:
    """Left button on empty space + drag: the video follows the pointer (turns left / right, tilts up / down)."""

    def __init__(self, video):
        self.video = video
        self._start = None

    def on_pointer(self, e):
        v = self.video
        if e.kind == "press" and e.button == "left":
            self._start = (v.front_yaw, v.shell.settings().video_tilt)
        elif e.kind == "drag" and self._start is not None:
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
                v.projection, v.stereo, v.shell.settings().private_mode, self.ctx.drag_start)

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

        if v.window:   # the browser plays it: jump around there
            progress = Label("From the browser window - put the video in full screen there for the best picture",
                             size=theme.SMALL - 4, color=theme.MUTED, w=CONTROLS_WIDTH, h=theme.ROW, align="center")
        else:
            progress = Row([Label(_time(position), size=theme.SMALL, color=theme.FG, w=150, h=theme.ROW,
                                  align="right"),
                            SeekBar("seek", position / duration if duration else 0.0, v.seek_fraction, h=theme.ROW,
                                    flex=1),
                            Label(_time(duration) if duration else "live", size=theme.SMALL, color=theme.MUTED,
                                  w=150, h=theme.ROW)],
                           w=CONTROLS_WIDTH, h=theme.ROW, gap=8)
        title = shown_title(v.info.title, private=v.shell.settings().private_mode)
        about = Column([Label(title, size=theme.SMALL, h=int(theme.ROW * 0.8), w=SIDE_WIDTH, indent=16),
                        Label(status, size=theme.SMALL - 4, color=color, h=int(theme.ROW * 0.7), w=SIDE_WIDTH,
                              indent=16)], w=SIDE_WIDTH, h=h)
        play = Button("play", icon=Icon.PAUSE if playing else Icon.PLAY, w=h + 14, h=h + 14, style="primary",
                      size=theme.TEXT, on_click=v.play_pause)
        if v.window:
            center = Row([play], h=h + 14)
        else:
            center = Row([button("back", f"−{SKIP_SECONDS}s", action=lambda: v.skip(-SKIP_SECONDS), w=150), play,
                          button("forward", f"+{SKIP_SECONDS}s", action=lambda: v.skip(SKIP_SECONDS), w=150)],
                         gap=18, h=h + 14)
        right = Row([Spacer(flex=1),
                     button("settings", icon=Icon.SETTINGS, action=v.toggle_settings, selected=v.settings_open,
                            w=h + 10),
                     button("recenter", icon=Icon.RECENTER, action=v.recenter_here, w=h + 10),
                     button("mouse", "PC", Icon.MOUSE, action=lambda: v.shell.dock_action("mouse_to_pc")),
                     button("exit", "Exit", Icon.CLOSE, action=v.shell.stop_video, style="danger")],
                    w=SIDE_WIDTH, h=h, gap=6)
        buttons = Row([about, Spacer(flex=1), center, Spacer(flex=1), right], w=CONTROLS_WIDTH, h=h + 14)
        return Column([progress, buttons], pad=18, gap=6, bg=theme.BG, radius=28)


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
        return (v.projection, v.stereo, v.recognizing, self.tab) + tuple(getattr(s, k) for k in VIDEO_DEFAULTS)

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
            if v.stereo != "mono":
                rows += [slider("video_ipd", "IPD (depth)", "{:+.1f}°"),
                         hint("IPD = how far the 3D feels:  ←  closer, bigger      farther, smaller  →")]
            else:
                rows.append(hint("Eye separation is for 3D videos (3D above)"))
            return Column(rows, pad=14, bg=theme.BG, radius=28)

        if self.tab == "picture":
            rows += [slider("video_brightness", "Brightness", "{:.0f}%", 100),
                     slider("video_contrast", "Contrast", "{:.0f}%", 100),
                     slider("video_saturation", "Colors", "{:.0f}%", 100),
                     slider("video_volume", "Volume", "{:.0f}%", 100)]
            return Column(rows, pad=14, bg=theme.BG, radius=28)

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
        return Column(rows, pad=14, bg=theme.BG, radius=28)


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
