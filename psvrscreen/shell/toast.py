"""Short messages floating low in front of you for a few seconds ("Saved", what you said and what it did), and
- while the microphone listens (press to talk) - what you're saying right now."""
import time

from ..core import geometry
from ..scene.surface import INTERFACE, Surface
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Label, Row

SECONDS = 3.5
DISTANCE = 1.4


class ToastView(View):
    def __init__(self, shell):
        super().__init__(distance=DISTANCE)
        self.shell = shell
        self.text, self.shown = "", -100.0

    def show(self, text):
        self.text, self.shown = text, time.monotonic()

    def _now_text(self):
        voice = self.shell.voice
        if voice is not None and voice.heard and not voice.listening:
            return Icon.MIC, voice.heard                     # "Understanding..."
        if voice is not None and voice.blocked:
            return Icon.MIC, "Windows asks to allow the microphone - answer Yes on the PC screen (Main monitor window)"
        if voice is not None and voice.listening:
            return Icon.MIC, (voice.heard + " ...") if voice.heard else "Listening... say a command"
        if time.monotonic() - self.shown < SECONDS:
            return Icon.INFO, self.text
        return None, ""

    def state(self):
        return self._now_text()

    def build(self):
        icon, text = self._now_text()
        return Row([Label(icon or Icon.INFO, size=theme.TEXT, color=theme.ACCENT_LIGHT, icon=True, w=60, h=theme.ROW),
                    Label(text, size=theme.SMALL, h=theme.ROW)], pad=16, gap=8, bg=theme.BG,
                   radius=theme.RADIUS_PANEL, border=theme.EDGE)

    def surfaces(self, now):
        _, text = self._now_text()
        if not text:
            return []
        self.refresh()
        sh = self.shell
        model = (geometry.rotate_y(-sh.look_yaw) @ geometry.rotate_x(min(sh.look_pitch, 10) - 22)
                 @ geometry.translate(0, 0, -DISTANCE))
        return [Surface("toast", model, self.width, self.height, texture=self.texture, layer=INTERFACE)]
