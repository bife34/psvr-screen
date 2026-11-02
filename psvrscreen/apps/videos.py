"""Videos: the VR video player's library. A grid of cards with thumbnails - the videos you opened lately (VLC,
Windows, Downloads / Videos / Desktop) or any drive's folders. Click a video to watch it around you
(shell/vr_video.py) with its kind recognised (video/recognize.py); right click: play it as a kind you choose.
Opened from Home - there's no dock button, so nobody sees it there.

In private mode Recent stays hidden and no thumbnails are written to disk.
"""
import threading
from pathlib import Path

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Card, Grid, Label, Panel, Row, Spacer
from ..video import files, recognize
from ..video.thumbs import THUMBNAILS
from .base import Page, ViewContent

PAGE_WIDTH = 1640
COLUMNS = 4
ROWS = 3
CARD_W = (PAGE_WIDTH - 2 * 32 - (COLUMNS - 1) * theme.GAP * 2) // COLUMNS
CARD_H = int(CARD_W * 9 / 16) + int(theme.SMALL * 2.6)
RECENT = "recent"
BADGE_COLORS = {"3D": (175, 82, 222, 255)}


def _kind(path):
    """Badges from the file's name (the picture is looked at when it's opened)."""
    tags = recognize.from_tags(path.stem)
    view = {"equi": "360°", "equi180": "180°", "fisheye": "Fisheye"}.get(tags.projection)
    badges = [(view, theme.ACCENT)] if view else []
    if tags.stereo in ("side_by_side", "top_bottom"):
        badges.append(("3D", BADGE_COLORS["3D"]))
    return badges


def _duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Library(Page):
    """A grid browser of places: Recent and the drives' folders. Subclasses say which files and what a click
    does (Videos, Photos)."""
    TITLE, ICON, EMPTY = "Videos", Icon.VIDEO, "No videos here"
    RECENT_LABEL, RECENT_ICON = "Recent", Icon.HISTORY
    RECENT_HINT = "Videos you opened lately, and the newest in Downloads, Videos and Desktop"
    RECENT_IS_PRIVATE = True       # private mode hides the first place

    def __init__(self, host):
        super().__init__(host)
        self.place = RECENT            # RECENT or a folder Path
        self.page = 0
        self.dirs, self.items = [], []
        self.loading = False
        self.drives = files.drives()
        self._load()

    # -- for subclasses --------------------------------------------------------------------------------
    def recent(self):
        return files.recent_videos()

    def first_place(self):
        """(folders, files) of the first place: Recent."""
        return [], self.recent()

    def folder(self, path):
        return files.folder_entries(path)

    def open(self, path):
        self.host.play_file(path)

    def menu(self, path):
        self.host.video_file_menu(path)

    def badges(self, path):
        return _kind(path)

    def card(self, key, path):
        """The card of a file in the grid."""
        image, seconds = THUMBNAILS.get(path)
        subtitle = " · ".join(x for x in (_duration(seconds), str(path.parent) if self.place == RECENT else "") if x)
        return Card(key, path.name, image, CARD_W, CARD_H, badges=self.badges(path), icon=self.ICON,
                    subtitle=subtitle or None, on_click=lambda: self.open(path), on_right=lambda: self.menu(path))

    def extra_buttons(self, h):
        """More buttons next to the folder's path."""
        return []

    # -- places ------------------------------------------------------------------------------------------
    def _load(self):
        """Read the current place in the background (a big folder or a slow drive must not stall VR)."""
        place = self.place
        self.loading, self.page = True, 0

        def read():
            if place == RECENT:
                dirs, items = self.first_place()
            else:
                dirs, items = self.folder(place)
            if place == self.place:
                self.dirs, self.items, self.loading = dirs, items, False

        threading.Thread(target=read, daemon=True, name="library-list").start()

    def go(self, place):
        self.place = place
        self.dirs, self.items = [], []
        self._load()

    def _private(self):
        return self.host.settings().private_mode

    def state(self):
        THUMBNAILS.private = self._private()
        return (str(self.place), self.page, self.loading, len(self.dirs), len(self.items), self._private(),
                THUMBNAILS.version)

    def _turn(self, step, pages):
        self.page = max(0, min(pages - 1, self.page + step))

    # -- the page ----------------------------------------------------------------------------------------
    def build(self):
        w, h = PAGE_WIDTH - 64, theme.ROW
        private = self._private()
        places = [Button("place:recent", self.RECENT_LABEL, self.RECENT_ICON, h=h, style="flat", selected=self.place == RECENT,
                         size=theme.TEXT - 4, on_click=lambda: self.go(RECENT))]
        for d in self.drives:
            here = self.place != RECENT and Path(self.place).anchor.lower() == d.lower()
            places.append(Button("drive:" + d, d.rstrip("\\"), Icon.DRIVE, h=h, style="flat", selected=here,
                                 size=theme.TEXT - 4, on_click=lambda d=d: self.go(Path(d))))
        rows = [Row([Label(self.ICON, size=theme.TITLE, color=theme.ACCENT_LIGHT, icon=True, h=int(h * 1.4), w=70),
                     Label(self.TITLE, size=theme.HUGE - 12, h=int(h * 1.4)), Spacer(flex=1), *places],
                    w=w, h=int(h * 1.4), gap=6)]
        if self.place == RECENT:
            rows.append(Row([Label(self.RECENT_HINT, size=theme.SMALL - 4, color=theme.MUTED, flex=1, h=h, indent=8)],
                            w=w, h=h))
        else:
            parent = Path(self.place).parent
            up = Button("up", "Up", Icon.UP, h=h - 8, style="normal", size=theme.TEXT - 6,
                        on_click=(lambda: self.go(parent)) if parent != Path(self.place) else None)
            rows.append(Row([up, Label(str(self.place), size=theme.SMALL - 4, color=theme.MUTED, flex=1, h=h,
                                       indent=12), *self.extra_buttons(h - 8)], w=w, h=h, gap=6))

        per_page = COLUMNS * ROWS
        grid_h = ROWS * CARD_H + (ROWS - 1) * theme.GAP * 2
        if self.place == RECENT and private and self.RECENT_IS_PRIVATE:
            rows.append(Label("Recent is hidden in private mode - open a drive above", size=theme.SMALL,
                              color=theme.MUTED, w=w, h=grid_h, align="center"))
            rows.append(Spacer(h=h))
            return Panel(rows, pad=32, gap=theme.GAP)

        entries = [("dir", p) for p in self.dirs] + [("item", p) for p in self.items]
        pages = max(1, (len(entries) + per_page - 1) // per_page)
        self.page = min(self.page, pages - 1)
        shown = entries[self.page * per_page:(self.page + 1) * per_page]
        cards = []
        for i, (kind, path) in enumerate(shown):
            key = f"card:{self.page}:{i}"
            if kind == "dir":
                cards.append(Card(key, path.name, None, CARD_W, CARD_H, icon=Icon.FOLDER,
                                  on_click=lambda p=path: self.go(p)))
                continue
            cards.append(self.card(key, path))
        if self.loading:
            body = Label("Looking...", size=theme.SMALL, color=theme.MUTED, w=w, h=grid_h, align="center")
        elif not cards:
            body = Label(self.EMPTY, size=theme.SMALL, color=theme.MUTED, w=w, h=grid_h, align="center")
        else:
            body = Row([Grid(cards, COLUMNS)], w=w, h=grid_h, align="start")
        rows.append(body)
        footer = [Spacer(flex=1)]
        if pages > 1:
            footer += [Button("page:-1", icon=Icon.PAGE_UP, w=h + 20, h=h - 8, style="normal",
                              on_click=lambda: self._turn(-1, pages)),
                       Label(f"{self.page + 1} / {pages}", size=theme.SMALL, color=theme.MUTED, h=h),
                       Button("page:1", icon=Icon.PAGE_DOWN, w=h + 20, h=h - 8, style="normal",
                              on_click=lambda: self._turn(1, pages))]
        rows.append(Row(footer, w=w, h=h, gap=12))
        return Panel(rows, pad=32, gap=theme.GAP)


class VideosApp(ViewContent):
    icon = Icon.VIDEO

    def __init__(self, panel, host):
        super().__init__(panel, host, Library(host))
