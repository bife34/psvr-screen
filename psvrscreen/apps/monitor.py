"""A whole monitor in VR: your main screen (for videos), or a "Desktop view" - an extra virtual monitor with
big text where you can arrange windows the classic way. Clicks and the wheel go to the matching spot."""
from ..render.gl import Texture
from ..ui import theme
from ..win import displays
from ..win.capture import MonitorCapture
from .base import Content
from .forwarding import ForwardedInput


class MonitorView(Content):
    icon = theme.Icon.MONITOR

    def __init__(self, panel, host):
        super().__init__(panel, host)
        self.kind = panel["source"]          # "main", "view1", "view2"
        self._capture = MonitorCapture(self.kind, fps=lambda: host.settings().fps)
        self._texture = None
        self._seq = -1
        self._input = ForwardedInput(self._rect, host.note_click)

    @property
    def texture(self):
        return self._texture

    def update(self, panel, now):
        seq, frame = self._capture.latest()
        if frame is not None and seq != self._seq:
            if self._texture is None:
                self._texture = Texture()
            self._texture.upload(frame, bgra=True)
            self._seq = seq

    def _rect(self):
        monitor = displays.find(self.kind)
        return monitor.rect if monitor else None

    def on_pointer(self, e):
        self._input.handle(e)

    def cancel_input(self):
        self._input.cancel()

    def request_close(self):
        self.closed = True   # closing a monitor only takes it out of VR

    def release(self):
        self.cancel_input()
        self._capture.stop()
        if self._texture is not None:
            self._texture.delete()
            self._texture = None
