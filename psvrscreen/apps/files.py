"""Files: your folders and drives in VR, every kind of file. Videos play around you, pictures open in the
picture viewer (with "View around me"), anything else opens in its Windows app - and that app's window comes
into VR. Right click: a file's menu. "Open in Explorer" shows the folder in File Explorer, in VR too.
"""
import time

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Card
from ..video import files
from .base import ViewContent
from .photos import PhotosPage
from .photos import _kind as picture_kind
from ..video.thumbs import THUMBNAILS
from .videos import CARD_H, CARD_W, RECENT, _duration
from .videos import _kind as video_kind

MUSIC_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"}
PROGRAM_EXTENSIONS = {".exe", ".lnk", ".url", ".bat", ".msi"}
KIND_COLOR = (99, 99, 110, 255)


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def _icon(path):
    ext = path.suffix.lower()
    if ext in files.EXTENSIONS:
        return Icon.VIDEO
    if ext in files.PICTURE_EXTENSIONS:
        return Icon.PHOTO
    if ext in MUSIC_EXTENSIONS:
        return Icon.MUSIC
    if ext in PROGRAM_EXTENSIONS:
        return Icon.APPS
    return Icon.FILE


class FilesPage(PhotosPage):
    TITLE, ICON, EMPTY = "Files", Icon.FOLDER, "This folder is empty"
    RECENT_LABEL, RECENT_ICON = "Home", Icon.HOME
    RECENT_HINT = "Your folders - or pick a drive above"
    RECENT_IS_PRIVATE = False
    BACK_LABEL = "Back to the folder"

    # -- the library's choices -----------------------------------------------------------------------------
    def place_name(self):
        return self.RECENT_LABEL, self.RECENT_ICON, self.RECENT_HINT

    def first_place(self):
        return files.home_folders(), []

    def folder(self, path):
        return files.folder_entries(path, None)

    def open(self, path):
        ext = path.suffix.lower()
        if ext in files.PICTURE_EXTENSIONS:
            super().open(path)
        elif ext in files.EXTENSIONS:
            self.host.play_file(path)
        else:
            self.host.open_file(path)

    def menu(self, path):
        self.host.file_menu(path)

    def badges(self, path):
        ext = path.suffix.lower()
        media = (video_kind(path) if ext in files.EXTENSIONS
                 else picture_kind(path) if ext in files.PICTURE_EXTENSIONS else [])
        return ([(ext[1:].upper(), KIND_COLOR)] if ext else []) + media

    def card(self, key, path):
        """Videos and pictures show their thumbnail; every file says its size and date."""
        image, seconds = None, None
        if path.suffix.lower() in files.EXTENSIONS | files.PICTURE_EXTENSIONS:
            image, seconds = THUMBNAILS.get(path)
        try:
            stat = path.stat()
            parts = [_duration(seconds), _size(stat.st_size),
                     time.strftime("%d %b %Y", time.localtime(stat.st_mtime))]
            subtitle = " · ".join(p for p in parts if p)
        except OSError:
            subtitle = None
        return Card(key, path.name, image, CARD_W, CARD_H, badges=self.badges(path), icon=_icon(path),
                    subtitle=subtitle, on_click=lambda: self.open(path), on_right=lambda: self.menu(path))

    def extra_buttons(self, h):
        if self.place == RECENT:
            return []
        place = self.place
        return [Button("explorer", "Open in Explorer", Icon.FOLDER, h=h, style="normal", size=theme.TEXT - 6,
                       on_click=lambda: self.host.open_file(place))]


class FilesApp(ViewContent):
    icon = Icon.FOLDER

    def __init__(self, panel, host):
        super().__init__(panel, host, FilesPage(host))
