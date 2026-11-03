"""Timeline in space: a video's whole length as a ribbon of its frames spiralling around you - the start on
your left, going round and up. Grab and drag to turn it, the wheel winds it along (through time); point at a
frame to bring it closer, click it and the video plays from there. (Videos, right click > Timeline in space;
the video bar's Timeline.)"""
import threading
from pathlib import Path

from PIL import ImageDraw

from ..core import geometry
from ..core.log import log_error
from ..render.gl import Texture
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Label, Panel, Row, Spacer
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT

FRAMES = 48
WIDTH = 480                     # px of each frame
DISTANCE = 2.4                  # m
FRAME_HEIGHT = 0.36             # m
TURNS = 2.0                     # the ribbon goes around you this many times
RISE = 0.5                      # m up per turn
GROW = 1.6                      # a frame you point at grows this much
WHEEL_DEGREES = 15.0


def _clock(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class _Frame:
    def __init__(self, ribbon, n, seconds, image):
        self.ribbon, self.n, self.seconds, self.image = ribbon, n, seconds, image
        self.texture, self.grow = None, 1.0

    def on_pointer(self, e):
        r = self.ribbon
        if e.kind == "move":
            r.hovered = self
        elif e.kind == "leave" and r.hovered is self:
            r.hovered = None
        elif e.kind == "wheel":
            r.wind(e.delta / 120)
        else:
            r.grab.on_pointer(e, self)


class TimelineRibbon:
    def __init__(self, shell, path):
        self.shell, self.path = shell, Path(path)
        self.frames, self.loading, self.error = [], True, ""
        self.duration = 0.0
        self.front_yaw = shell.look_yaw
        self.turn = 0.0                  # degrees the ribbon was wound (+ = later frames come to the front)
        self.hovered = None
        self.grab = _Grab(self)
        self.bar = TimelineBar(self)
        self._last = None
        threading.Thread(target=self._read, daemon=True, name="timeline").start()

    def _read(self):
        import av
        from ..video import thumbs
        try:
            with av.open(str(self.path)) as container:
                stream = container.streams.video[0]
                self.duration = float(container.duration or 0) / 1_000_000
                for n in range(FRAMES):
                    at = self.duration * (n + 0.5) / FRAMES
                    container.seek(int(at * 1_000_000))
                    for k, frame in enumerate(container.decode(stream)):   # from the key frame to the moment
                        if (frame.time or 0.0) >= at - 0.05 or k > 240:
                            break
                    image = thumbs._one_eye(frame.to_image(), self.path)
                    image.thumbnail((WIDTH, WIDTH))
                    image = image.convert("RGBA")
                    d = ImageDraw.Draw(image)
                    label, font = _clock(frame.time or at), theme.font(max(18, image.height // 9))
                    w = d.textlength(label, font=font)
                    d.rounded_rectangle((8, 8, 32 + w, 16 + font.size * 1.3), 10, fill=(10, 12, 16, 200))
                    d.text((20, 12), label, font=font, fill=theme.FG)
                    self.frames.append(_Frame(self, n, frame.time or at, image))
        except Exception:
            log_error(f"timeline {self.path.name}")
            self.error = "Can't read this video's frames"
        self.loading = False

    def close(self):
        for f in self.frames:
            if f.texture is not None:
                f.texture.delete()
        self.bar.release()

    def recentered(self):
        self.front_yaw = 0.0

    def wind(self, notches):
        self.turn = max(-30.0, min(TURNS * 360.0 - 120.0, self.turn + notches * WHEEL_DEGREES))

    def play(self, frame):
        self.shell.stop_timeline()
        self.shell.watch_file(self.path, start=max(0.0, frame.seconds - 1.0))

    def surfaces(self, now):
        dt = min(0.1, now - self._last) if self._last else 0.0
        self._last = now
        out = []
        frames = list(self.frames)
        uploads = 4
        for f in frames:
            if f.texture is None and uploads:
                f.texture = Texture()
                f.texture.upload(f.image)
                uploads -= 1
        span = TURNS * 360.0
        for f in frames:
            if f.texture is None:
                continue
            k = f.n / (FRAMES - 1)
            angle = -120.0 + k * span - self.turn              # degrees: - = left of the front
            height = k * TURNS * RISE - (self.turn / 360.0) * RISE - 0.2      # rises along the spiral
            target = GROW if f is self.hovered else 1.0
            f.grow += (target - f.grow) * min(1.0, 10 * dt) if dt else target - f.grow
            w = FRAME_HEIGHT * f.image.width / f.image.height * f.grow
            model = (geometry.rotate_y(-self.front_yaw) @ geometry.translate(0, height, 0)
                     @ geometry.rotate_y(-angle) @ geometry.translate(0, 0, -DISTANCE + 0.25 * (f.grow - 1)))
            out.append(Surface(f"timeline:{f.n}", model, w, FRAME_HEIGHT * f.grow, texture=f.texture, target=f))
        if self.shell.pointer.active or self.loading:
            self.bar.refresh()
            model = (geometry.rotate_y(-self.front_yaw) @ geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE)
                     @ geometry.tilt(DOCK_TILT))
            out.append(Surface("timeline-bar", model, self.bar.width, self.bar.height, texture=self.bar.texture,
                               layer=INTERFACE, target=self.bar, data={"ui": True}))
        return out


class _Grab:
    """Drag: left / right winds the ribbon through time; a click on a frame plays from there."""

    def __init__(self, ribbon):
        self.ribbon = ribbon
        self._press = None

    def on_pointer(self, e, frame=None):
        r = self.ribbon
        if e.kind == "press":
            self._press = (frame, e.button, e.yaw, r.turn, False)
        elif e.kind == "drag" and self._press is not None:
            frame0, button, yaw0, turn0, moved = self._press
            if moved or abs(e.yaw - yaw0) > 1.0:
                r.turn = max(-30.0, min(TURNS * 360.0 - 120.0, turn0 - (e.yaw - yaw0)))
                self._press = (frame0, button, yaw0, turn0, True)
        elif e.kind == "release" and self._press is not None:
            frame0, button, _, _, moved = self._press
            self._press = None
            if not moved and frame0 is not None and e.on_surface and button == "left":
                r.play(frame0)


class TimelineBar(View):
    def __init__(self, ribbon):
        super().__init__(distance=DOCK_DISTANCE)
        self.ribbon = ribbon

    def state(self):
        r = self.ribbon
        return (len(r.frames), r.loading, r.error, r.hovered.n if r.hovered else None,
                r.shell.settings().private_mode)

    def build(self):
        r = self.ribbon
        h = theme.BAR - 8
        title = "Video" if r.shell.settings().private_mode else r.path.name
        if r.error:
            status = r.error
        elif r.loading:
            status = f"Reading its frames  {len(r.frames)} / {FRAMES}"
        elif r.hovered is not None:
            status = f"Click to play from {_clock(r.hovered.seconds)}"
        else:
            status = "Drag or wheel: through time  ·  click a frame: play from there"
        return Panel([Row([Label(Icon.HISTORY, size=theme.TITLE, color=theme.ACCENT_LIGHT, icon=True, w=70, h=h),
                           Label(title, size=theme.SMALL, w=520, h=h),
                           Label(status, size=theme.SMALL - 6, color=theme.MUTED, flex=1, h=h),
                           Spacer(w=12),
                           Button("exit", "Exit", Icon.CLOSE, h=h, style="danger", size=theme.TEXT - 4,
                                  on_click=r.shell.stop_timeline)], w=1500, h=h, gap=8)], pad=20)
