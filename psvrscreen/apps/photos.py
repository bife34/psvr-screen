"""Photos: your pictures in a grid (newest in Pictures / Desktop / Downloads, or any drive's folders), a viewer
with previous / next, and "View around me" - 360° / 180° / 3D pictures shown around you like a VR video.
"""
import threading

from PIL import Image

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Label, Panel, Picture, Row, Spacer
from ..video import files, recognize
from .base import ViewContent
from .videos import CARD_H, PAGE_WIDTH, ROWS, Library

VIEW_SIZE = 2400                  # the viewer's picture is decoded this big at most


def _kind(path, image=None):
    """Badges: what the name says, or a picture twice as wide as tall (360°)."""
    tags = recognize.from_tags(path.stem)
    if tags.projection is None and image is not None and 1.9 < image.width / image.height < 2.1:
        return [("360°", theme.ACCENT)]
    view = {"equi": "360°", "equi180": "180°", "fisheye": "Fisheye"}.get(tags.projection)
    return [(view, theme.ACCENT)] if view else []


class PhotosPage(Library):
    TITLE, ICON, EMPTY = "Photos", Icon.PHOTO, "No pictures here"
    RECENT_HINT = "The newest pictures in Pictures, Desktop and Downloads"

    def __init__(self, host):
        super().__init__(host)
        self.viewing = None           # index into self.items while a picture is open
        self.big = None               # (path, its picture at viewer size)

    # -- the library's choices -----------------------------------------------------------------------------
    def recent(self):
        return files.recent_photos()

    def folder(self, path):
        return files.folder_entries(path, files.PICTURE_EXTENSIONS)

    def open(self, path):
        self.viewing = self.items.index(path)
        self._load_big(path)

    def menu(self, path):
        self.open(path)

    def badges(self, path):
        return _kind(path)

    def go(self, place):
        self.viewing = None
        super().go(place)

    # -- the viewer ------------------------------------------------------------------------------------------
    def _load_big(self, path):
        def load():
            try:
                with Image.open(path) as image:
                    image.draft("RGB", (VIEW_SIZE, VIEW_SIZE))
                    image = image.convert("RGB")
                    image.thumbnail((VIEW_SIZE, VIEW_SIZE))
                self.big = (path, image)
            except Exception:
                self.big = (path, None)

        self.big = None
        threading.Thread(target=load, daemon=True, name="photo-view").start()

    def _step(self, step):
        if self.items:
            self.viewing = (self.viewing + step) % len(self.items)
            self._load_big(self.items[self.viewing])

    def _close(self):
        self.viewing, self.big = None, None

    def state(self):
        big = self.big[0] if self.big else None
        return super().state() + (self.viewing, str(big))

    def build(self):
        if self.viewing is None or self.viewing >= len(self.items):
            return super().build()
        path = self.items[self.viewing]
        image = self.big[1] if self.big and self.big[0] == path else None
        w, h = PAGE_WIDTH - 64, theme.ROW
        picture_h = ROWS * CARD_H + (ROWS - 1) * theme.GAP * 2 + h
        name = path.name if not self._private() else "Photo"
        header = Row([Button("back", "All photos", Icon.BACK, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=self._close),
                      Label(name, size=theme.TEXT - 4, flex=1, h=h, indent=16),
                      Label(f"{self.viewing + 1} / {len(self.items)}", size=theme.SMALL, color=theme.MUTED, h=h)],
                     w=w, h=int(h * 1.4), gap=12)
        footer = Row([Button("prev", "Previous", Icon.PREVIOUS, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=lambda: self._step(-1)),
                      Spacer(flex=1),
                      Button("around", "View around me", Icon.SPHERE, h=h, style="primary", size=theme.TEXT - 6,
                             on_click=lambda: self.host.view_photo(path)),
                      Spacer(flex=1),
                      Button("next", "Next", Icon.NEXT, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=lambda: self._step(1))], w=w, h=int(h * 1.2), gap=12)
        return Panel([header, Picture(image, w, picture_h - int(h * 1.2), fit=True, icon=Icon.PHOTO), footer],
                     pad=32, gap=theme.GAP)


class PhotosApp(ViewContent):
    icon = Icon.PHOTO

    def __init__(self, panel, host):
        super().__init__(panel, host, PhotosPage(host))
