"""Photos: your pictures in a grid (newest in Pictures / Desktop / Downloads, or any drive's folders), a viewer
with previous / next, "View around me" - 360° / 180° / 3D pictures shown around you like a VR video - and
"Photo wall": a folder's pictures side by side around you (shell/photo_wall.py).
"""
import threading
from pathlib import Path

from PIL import Image

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Label, Panel, Picture, Row, Spacer
from ..video import files, recognize

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
    FOLDER_NAMES = ("Pictures", "Imagens", "Desktop", "Ambiente de Trabalho", "Downloads", "Transferências")
    BACK_LABEL = "All photos"

    def __init__(self, host):
        super().__init__(host)
        self.viewing = None           # index into self.pictures() while a picture is open
        self.big = None               # (path, its picture at viewer size)

    # -- the library's choices -----------------------------------------------------------------------------
    def recent(self):
        return files.recent_photos()

    def folder(self, path):
        return files.folder_entries(path, files.PICTURE_EXTENSIONS)

    def open(self, path):
        self.viewing = self.pictures().index(path)
        self._load_big(path)

    def menu(self, path):
        self.host.photo_menu(path, lambda: self.open(path), recent=self._in_recent())

    def badges(self, path):
        return _kind(path)

    def extra_buttons(self, h):
        folder = Path(self.place)
        return [Button("wall", "Photo wall", Icon.GALLERY, h=h, style="primary", size=theme.TEXT - 6,
                       on_click=lambda: self.host.view_wall(folder)),
                Button("rooms", "Rooms", Icon.STAR, h=h, style="normal", size=theme.TEXT - 6,
                       on_click=self.host.rooms_menu)]

    def go(self, place):
        self.viewing = None
        super().go(place)

    # -- the viewer ------------------------------------------------------------------------------------------
    def pictures(self):
        """What previous / next go through: the place's pictures."""
        return [p for p in self.items if p.suffix.lower() in files.PICTURE_EXTENSIONS]

    def _load_big(self, path):
        def load():
            try:
                with Image.open(files.source(path)) as image:     # (a safe's photo too)
                    image.draft("RGB", (VIEW_SIZE, VIEW_SIZE))
                    image = image.convert("RGB")
                    image.thumbnail((VIEW_SIZE, VIEW_SIZE))
                turns = self.host.settings().photo_turns.get(files.turn_key(path), 0)
                self.big = (path, image.rotate(-90 * turns, expand=True) if turns else image)
            except Exception:
                self.big = (path, None)

        self.big = None
        threading.Thread(target=load, daemon=True, name="photo-view").start()

    def _step(self, step):
        pictures = self.pictures()
        if pictures:
            self.viewing = (self.viewing + step) % len(pictures)
            self._load_big(pictures[self.viewing])

    def _turn(self, step):
        """Turn the photo a quarter right (+1) / left (-1); remembered for it (not in private mode or the safe)."""
        if not self.big or self.big[1] is None:
            return
        path, image = self.big
        self.big = (path, image.rotate(-90 * step, expand=True))
        if not self._private() and not files.in_vault(path):
            turns = dict(self.host.settings().photo_turns)
            key = files.turn_key(path)
            turns[key] = (turns.get(key, 0) + step) % 4
            if not turns[key]:
                del turns[key]
            self.host.set_setting("photo_turns", turns)
        self._turned = getattr(self, "_turned", 0) + 1      # (redraw)

    def _close(self):
        self.viewing, self.big = None, None

    def state(self):
        big = self.big[0] if self.big else None
        return super().state() + (self.viewing, str(big), getattr(self, "_turned", 0))

    def build(self):
        pictures = self.pictures()
        if self.viewing is None or self.viewing >= len(pictures):
            return super().build()
        path = pictures[self.viewing]
        image = self.big[1] if self.big and self.big[0] == path else None
        w, h = PAGE_WIDTH - 64, theme.ROW
        picture_h = ROWS * CARD_H + (ROWS - 1) * theme.GAP * 2 + h
        name = path.name if not self._private() else "Photo"
        header = Row([Button("back", self.BACK_LABEL, Icon.BACK, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=self._close),
                      Label(name, size=theme.TEXT - 4, flex=1, h=h, indent=16),
                      Button("turn-left", "Turn left", None, h=h, style="flat", size=theme.TEXT - 6,
                             on_click=lambda: self._turn(-1)),
                      Button("turn-right", "Turn right", None, h=h, style="flat", size=theme.TEXT - 6,
                             on_click=lambda: self._turn(1)),
                      Label(f"{self.viewing + 1} / {len(pictures)}", size=theme.SMALL, color=theme.MUTED, h=h)],
                     w=w, h=int(h * 1.4), gap=12)
        footer = Row([Button("prev", "Previous", Icon.PREVIOUS, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=lambda: self._step(-1)),
                      Spacer(flex=1),
                      Button("around", "View around me", Icon.SPHERE, h=h, style="primary", size=theme.TEXT - 6,
                             on_click=lambda: self.host.view_photo(path, pictures)),
                      Button("wall", "Photo wall", Icon.GALLERY, h=h, style="primary", size=theme.TEXT - 6,
                             on_click=lambda: self.host.view_wall(path.parent)),
                      Spacer(flex=1),
                      Button("next", "Next", Icon.NEXT, h=h, style="normal", size=theme.TEXT - 6,
                             on_click=lambda: self._step(1))], w=w, h=int(h * 1.2), gap=12)
        return Panel([header, Picture(image, w, picture_h - int(h * 1.2), fit=True, icon=Icon.PHOTO), footer],
                     pad=32, gap=theme.GAP)
