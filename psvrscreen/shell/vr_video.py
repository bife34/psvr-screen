"""VR videos: noticing them in browser windows, and watching them around you.

VideoDetector (background thread) reads the address of every browser window that shows YouTube and asks
YouTube whether that video is a VR video. If it is, a "Watch in VR" button appears under the window (in VR)
or above the dock (a browser on the main screen).

ImmersiveVideo is the video mode: the video plays on a sphere around you (render/video_sphere.py) instead
of the room and the windows; its controls float below in front of the video while the pointer is in VR, and
their ⚙ opens the video settings (view, 3D, size, distance, brightness, volume - all sliders).
The browser's own playback is paused when it starts.
"""
import dataclasses
import math
import threading

from ..core import geometry
from ..core.settings import LIMITS, Settings, clamp
from ..render.video_sphere import VideoTexture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Label, Row, SeekBar, Slider, Spacer
from ..video import youtube
from ..video.player import VideoPlayer
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
CHECK_SECONDS = 2.0
CONTROLS_SECONDS = 4.0             # controls stay this long after the pointer last moved
CONTROLS_WIDTH = 1500
SIDE_WIDTH = 520                   # controls: title on the left, settings / exit on the right
PANEL_WIDTH = 1120                 # video settings
PANEL_GAP = 0.02                   # m between the controls and the settings above them
SKIP_SECONDS = 10
PROJECTION_NAMES = {"equi": "360°", "equi180": "180°", "mesh": "360°", "screen": "Screen"}
STEREO_NAMES = {"mono": "2D", "top_bottom": "3D ↕", "side_by_side": "3D ↔"}
VIDEO_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Settings) if f.name.startswith("video_")}


def is_browser_video(exe, title):
    return (exe or "").lower() in BROWSERS and "youtube" in (title or "").lower()


class VideoDetector(threading.Thread):
    """Set `candidates` = [hwnd] (browser windows showing YouTube); read `found` = {hwnd: VideoInfo} for the
    ones showing a VR video."""

    def __init__(self):
        super().__init__(daemon=True, name="vr-video-detector")
        self.candidates = []
        self.found = {}
        self._infos = {}                 # video id -> VideoInfo or None (not a VR video)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        from ..win.browser import BrowserReader
        reader = BrowserReader()
        try:
            while not self._stop.wait(CHECK_SECONDS):
                found = {}
                for hwnd in list(self.candidates):
                    vid = youtube.video_id(reader.page_address(hwnd))
                    if vid is None:
                        continue
                    if vid not in self._infos:
                        try:
                            self._infos[vid] = youtube.probe(vid)
                        except Exception:
                            continue     # network / YouTube trouble: try again next time
                    if self._infos[vid] is not None:
                        found[hwnd] = self._infos[vid]
                self.found = found
        finally:
            reader.close()


class WatchButton(View):
    """For a browser window (hwnd) that shows a VR video: under it in VR, or above the dock with the video's
    title (`with_title`) when the browser is on the main screen."""

    def __init__(self, shell, hwnd, distance, with_title=False):
        super().__init__(distance=distance)
        self.shell, self.hwnd, self.with_title = shell, hwnd, with_title

    def state(self):
        info = self.shell.video_detector.found.get(self.hwnd)
        return info and (info.video_id, info.projection, info.stereo)

    def build(self):
        info = self.shell.video_detector.found.get(self.hwnd)
        if info is None:
            return Row([], w=1, h=1)
        kind = (STEREO_NAMES[info.stereo] + " " if info.stereo != "mono" else "") + PROJECTION_NAMES[info.projection]
        text = f"Watch in VR  ·  {kind}" + (f"  ·  {info.title}" if self.with_title else "")
        return Row([Button("watch", text, Icon.PLAY, h=theme.BAR - 12, w=1400 if self.with_title else None,
                           selected=True, on_click=lambda: self.shell.watch_video(self.hwnd))],
                   pad=6, bg=theme.BG, radius=theme.RADIUS)


class ImmersiveVideo:
    def __init__(self, shell, info):
        self.shell, self.info = shell, info
        self.projection, self.stereo = info.projection, info.stereo
        self.front_yaw = shell.look_yaw          # the video's front is where you looked when it started
        self.player = VideoPlayer(info.video_url, info.audio_url, info.duration)
        self.player.volume = shell.settings().video_volume
        self.texture = VideoTexture()
        self.controls = Controls(self)
        self.panel = VideoSettings(self)
        self.settings_open = False
        self._pointer_seen = (None, 0.0)         # last pointer direction and when it changed

    def close(self):
        self.player.close()
        self.texture.delete()
        self.controls.release()
        self.panel.release()

    def recentered(self):
        self.front_yaw = 0.0

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

    def set_stereo(self, stereo):
        self.stereo = stereo

    def toggle_settings(self):
        self.settings_open = not self.settings_open

    def set_value(self, key, value):
        """A video setting (saved: the next video starts with it too)."""
        self.shell.store.update(**{key: clamp(key, value) if key in LIMITS else value})
        if key == "video_volume":
            self.player.volume = value

    def reset_settings(self):
        self.shell.store.update(**VIDEO_DEFAULTS)
        self.player.volume = VIDEO_DEFAULTS["video_volume"]

    # -- every frame ---------------------------------------------------------------------------------
    def surfaces(self, now):
        frame = self.player.frame()
        if frame is not None:
            self.texture.upload(frame)
        s = self.shell.settings()
        front = geometry.rotate_y(-self.front_yaw)
        out = [Surface("video", front, 1, 1, texture=self.texture, shape="sphere",   # centered between your eyes
                       data={"projection": self.projection, "stereo": self.stereo, "distance": s.video_distance,
                             "screen_degrees": s.video_screen_degrees, "screen_curve": s.video_screen_curve,
                             "size": s.video_size, "brightness": s.video_brightness,
                             "move_away": s.video_move_away})]
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


def _time(seconds):
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Controls(View):
    """  0:41 ━━━━━━━━━━━●──────────────── 7:52
         Title · status          ⟲10  (▶)  10⟳          ⚙  ⟳  ✕ Exit   """

    def __init__(self, video):
        super().__init__(distance=DOCK_DISTANCE)
        self.video = video

    def state(self):
        v, p = self.video, self.video.player
        return (int(p.position), p.paused, p.ended, p.buffering, p.error, v.settings_open, self.ctx.drag_start)

    def build(self):
        v, p = self.video, self.video.player
        duration = v.info.duration
        position = p.position
        h = theme.BAR - 8
        if p.error:
            status, color = "Can't play: " + p.error, theme.DANGER
        elif p.buffering:
            status, color = "Loading...", theme.ACCENT_LIGHT
        else:
            status, color = PROJECTION_NAMES[v.projection] + ("  ·  3D" if v.stereo != "mono" else ""), theme.MUTED
        playing = not p.paused and not p.ended

        def button(key, text=None, icon=None, action=None, style="flat", selected=False, w=None):
            return Button(key, text, icon, h=h, w=w, style=style, selected=selected, size=theme.TEXT - 4,
                          on_click=action)

        progress = Row([Label(_time(position), size=theme.SMALL, color=theme.FG, w=150, h=theme.ROW, align="right"),
                        SeekBar("seek", position / duration if duration else 0.0, v.seek_fraction, h=theme.ROW,
                                flex=1),
                        Label(_time(duration) if duration else "live", size=theme.SMALL, color=theme.MUTED, w=150,
                              h=theme.ROW)],
                       w=CONTROLS_WIDTH, h=theme.ROW, gap=8)
        about = Column([Label(v.info.title, size=theme.SMALL, h=int(theme.ROW * 0.8), w=SIDE_WIDTH, indent=16),
                        Label(status, size=theme.SMALL - 4, color=color, h=int(theme.ROW * 0.7), w=SIDE_WIDTH,
                              indent=16)], w=SIDE_WIDTH, h=h)
        center = Row([button("back", f"−{SKIP_SECONDS}s", action=lambda: v.skip(-SKIP_SECONDS), w=150),
                      Button("play", icon=Icon.PAUSE if playing else Icon.PLAY, w=h + 14, h=h + 14,
                            style="primary", size=theme.TEXT, on_click=v.play_pause),
                      button("forward", f"+{SKIP_SECONDS}s", action=lambda: v.skip(SKIP_SECONDS), w=150)],
                     gap=18, h=h + 14)
        right = Row([Spacer(flex=1),
                     button("settings", icon=Icon.SETTINGS, action=v.toggle_settings, selected=v.settings_open,
                            w=h + 10),
                     button("recenter", icon=Icon.RECENTER, action=v.shell.recenter, w=h + 10),
                     button("exit", "Exit", Icon.CLOSE, action=v.shell.stop_video, style="danger")],
                    w=SIDE_WIDTH, h=h, gap=6)
        buttons = Row([about, Spacer(flex=1), center, Spacer(flex=1), right], w=CONTROLS_WIDTH, h=h + 14)
        return Column([progress, buttons], pad=18, gap=6, bg=theme.BG, radius=28)


class VideoSettings(View):
    """⚙ of the video controls: how the video is shown. Everything except View and 3D is remembered."""

    def __init__(self, video):
        super().__init__(distance=DOCK_DISTANCE)
        self.video = video

    def state(self):
        v, s = self.video, self.video.shell.settings()
        return (v.projection, v.stereo) + tuple(getattr(s, k) for k in VIDEO_DEFAULTS)

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

        def slider(key, label, fmt, scale=1.0):
            lo, hi = LIMITS[key]
            return Slider(key, label, getattr(s, key) * scale, lo * scale, hi * scale, fmt, w=w,
                          on_change=lambda value: v.set_value(key, value / scale))

        def distance_slider():
            """Logarithmic: as many steps from 0.3 to 1 m as from 30 to 100 m."""
            lo, hi = LIMITS["video_distance"]
            return Slider("video_distance", "Distance", math.log(s.video_distance), math.log(lo), math.log(hi),
                          w=w, text=lambda value: f"{math.exp(value):.2f} m" if value < math.log(10)
                          else f"{math.exp(value):.0f} m",
                          on_change=lambda value: v.set_value("video_distance", math.exp(value)))

        sphere_only = ("screen",) if v.projection == "mesh" else ()
        views = [("screen", "Screen"), ("equi180", "180°"), ("equi", "360°")]
        current_view = "equi" if v.projection == "mesh" else v.projection
        rows = [Row([Label("Video settings", size=theme.TITLE, color=theme.ACCENT, flex=1, indent=24),
                     Button("reset", "Reset", h=theme.ROW - 8, style="flat", size=theme.TEXT - 6,
                            on_click=v.reset_settings)], w=w, h=int(theme.ROW * 1.2)),
                choices("View", views, current_view, v.set_projection,
                        disabled=sphere_only + (("equi180",) if v.projection == "mesh" else ())),
                choices("3D", [("mono", "Off"), ("side_by_side", "Side by side"), ("top_bottom", "Top / bottom")],
                        v.stereo, v.set_stereo)]
        if v.projection == "screen":
            rows += [slider("video_screen_degrees", "Screen size", "{:.0f}°"),
                     slider("video_screen_curve", "Curve", "{:.0f}%", 100)]
        else:
            rows.append(slider("video_size", "Size", "{:.2f}x"))
        def move_away_slider():
            """Logarithmic too (from 0): fine steps close by, big ones far away."""
            hi = math.log1p(LIMITS["video_move_away"][1])
            return Slider("video_move_away", "Move away", math.log1p(s.video_move_away), 0.0, hi, w=w,
                          text=lambda value: f"{math.expm1(value):.2f} m" if value < math.log1p(10)
                          else f"{math.expm1(value):.0f} m",
                          on_change=lambda value: v.set_value("video_move_away", math.expm1(value)))

        rows += [move_away_slider(),
                 Label("Move away: the screens around you go farther back - and look smaller", size=theme.SMALL - 6,
                       color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24),
                 distance_slider(),
                 Label("Distance: deeper or nearer for your eyes, same size", size=theme.SMALL - 6,
                       color=theme.MUTED, w=w, h=int(theme.ROW * 0.6), indent=24)]
        rows += [slider("video_brightness", "Brightness", "{:.0f}%", 100),
                 slider("video_volume", "Volume", "{:.0f}%", 100)]
        return Column(rows, pad=14, bg=theme.BG, radius=28)
