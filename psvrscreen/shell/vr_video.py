"""VR videos: noticing them in browser windows, and watching them around you.

VideoDetector (background thread) reads the address of every browser window that shows YouTube and asks
YouTube whether that video is a VR video. If it is, a "Watch in VR" button appears under the window (in VR)
or above the dock (a browser on the main screen).

ImmersiveVideo is the video mode: the video plays on a sphere around you (render/video_sphere.py) instead
of the room and the windows; its controls float below in front of the video while the pointer is in VR.
The browser's own playback is paused when it starts.
"""
import threading

from ..core import geometry
from ..core.settings import clamp
from ..render.video_sphere import VideoTexture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Label, Row, SeekBar
from ..video import youtube
from ..video.player import VideoPlayer
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
CHECK_SECONDS = 2.0
CONTROLS_SECONDS = 4.0             # controls stay this long after the pointer last moved
CONTROLS_WIDTH = 1500
SKIP_SECONDS = 10
SCREEN_STEP_DEGREES = 10           # "screen" videos: - / + makes the screen this much smaller / bigger
PROJECTION_NAMES = {"equi": "360°", "equi180": "180°", "mesh": "360°", "screen": "Screen"}
PROJECTION_NEXT = {"screen": "equi180", "equi180": "equi", "equi": "screen"}
STEREO_NAMES = {"mono": "2D", "top_bottom": "3D ↕", "side_by_side": "3D ↔"}
STEREO_NEXT = {"mono": "top_bottom", "top_bottom": "side_by_side", "side_by_side": "mono"}


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
        self.texture = VideoTexture()
        self.controls = Controls(self)
        self._pointer_seen = (None, 0.0)         # last pointer direction and when it changed

    def close(self):
        self.player.close()
        self.texture.delete()
        self.controls.release()

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

    def switch_projection(self):
        if self.projection != "mesh":     # the cube layout is only ever 360°
            self.projection = PROJECTION_NEXT[self.projection]

    def switch_stereo(self):
        self.stereo = STEREO_NEXT[self.stereo]

    def resize_screen(self, step):
        store = self.shell.store
        store.update(video_screen_degrees=clamp("video_screen_degrees",
                                                store.get().video_screen_degrees + step * SCREEN_STEP_DEGREES))

    # -- every frame ---------------------------------------------------------------------------------
    def surfaces(self, now):
        frame = self.player.frame()
        if frame is not None:
            self.texture.upload(frame)
        front = geometry.rotate_y(-self.front_yaw)
        out = [Surface("video", front, 1, 1, texture=self.texture, shape="sphere",
                       data={"projection": self.projection, "stereo": self.stereo,
                             "screen_degrees": self.shell.settings().video_screen_degrees})]
        if self._controls_visible(now):
            self.controls.refresh()
            model = front @ geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ geometry.tilt(DOCK_TILT)
            out.append(Surface("video-controls", model, self.controls.width, self.controls.height,
                               texture=self.controls.texture, layer=INTERFACE, target=self.controls,
                               data={"ui": True}))
        return out

    def _controls_visible(self, now):
        pointer = self.shell.pointer
        if not pointer.active:
            return False
        direction = (round(pointer.yaw, 3), round(pointer.pitch, 3))
        if direction != self._pointer_seen[0]:
            self._pointer_seen = (direction, now)
        on_controls = pointer.hit is not None and pointer.hit[0].id == "video-controls"
        return (self.player.paused or self.player.ended or on_controls
                or now - self._pointer_seen[1] < CONTROLS_SECONDS)


def _time(seconds):
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Controls(View):
    """Title / status, then: play-pause, -10 s, +10 s, time, seek bar, screen size (- / +, screen videos),
    360°/180°/Screen, 2D/3D, recenter, exit."""

    def __init__(self, video):
        super().__init__(distance=DOCK_DISTANCE)
        self.video = video

    def state(self):
        v, p = self.video, self.video.player
        return (int(p.position), p.paused, p.ended, p.buffering, p.error, v.projection, v.stereo,
                v.shell.settings().video_screen_degrees, self.ctx.drag_start)

    def build(self):
        v, p = self.video, self.video.player
        h = theme.BAR - 12
        duration = v.info.duration
        if p.error:
            status, color = "Can't play: " + p.error, theme.DANGER
        elif p.buffering:
            status, color = "Loading...", theme.MUTED
        else:
            status, color = "", theme.MUTED
        playing = not p.paused and not p.ended
        position = p.position
        time_text = _time(position) + (" / " + _time(duration) if duration else "")
        title = Row([Label(v.info.title, size=theme.SMALL, h=theme.ROW, flex=1, indent=24),
                     Label(status, size=theme.SMALL, color=color, h=theme.ROW),
                     Label(time_text, size=theme.SMALL, color=theme.MUTED, h=theme.ROW)],
                    w=CONTROLS_WIDTH, h=theme.ROW, gap=24)

        def button(key, text=None, icon=None, action=None, style="flat"):
            return Button(key, text, icon, h=h, style=style, size=theme.TEXT - 4, on_click=action)

        size = []
        if v.projection == "screen":
            size = [button("smaller", "−", action=lambda: v.resize_screen(-1)),
                    Label(f"{v.shell.settings().video_screen_degrees:.0f}°", size=theme.SMALL, color=theme.MUTED, h=h),
                    button("bigger", "+", action=lambda: v.resize_screen(1))]
        row = Row([button("play", icon=Icon.PAUSE if playing else Icon.PLAY, action=v.play_pause),
                   button("back", f"−{SKIP_SECONDS}s", action=lambda: v.skip(-SKIP_SECONDS)),
                   button("forward", f"+{SKIP_SECONDS}s", action=lambda: v.skip(SKIP_SECONDS)),
                   SeekBar("seek", position / duration if duration else 0.0, v.seek_fraction, h=h, flex=1),
                   *size,
                   button("projection", PROJECTION_NAMES[v.projection], action=v.switch_projection),
                   button("stereo", STEREO_NAMES[v.stereo], action=v.switch_stereo),
                   button("recenter", icon=Icon.RECENTER, action=v.shell.recenter),
                   button("exit", "Exit", Icon.CLOSE, action=v.shell.stop_video, style="danger")],
                  w=CONTROLS_WIDTH, h=theme.BAR, gap=6)
        return Column([title, row], pad=8, bg=theme.BG, radius=theme.RADIUS)
