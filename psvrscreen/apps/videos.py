"""Videos: the VR video player's library. Browse the PC's drives and folders, or the videos you opened lately
(VLC, Windows, Downloads / Videos / Desktop); pick one and it plays around you (shell/vr_video.py) with its
kind recognised (video/recognize.py); right click: play it as a kind you choose. Opened from Apps - there's no dock button, so nobody sees it there.

In private mode the Recent list stays hidden and the names of files are not shown in it.
"""
import threading
from pathlib import Path

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Column, Label, Row, Spacer
from ..video import files, recognize
from .base import Page, ViewContent

PAGE_WIDTH = 1600
ROWS_PER_PAGE = 9
RECENT = "recent"


def _kind(path):
    """What the file's name says it is (the picture is looked at when it's opened)."""
    tags = recognize.from_tags(path.stem)
    return " ".join(x for x in ({"equi": "360°", "equi180": "180°", "fisheye": "Fisheye"}.get(tags.projection),
                                {"side_by_side": "3D ↔", "top_bottom": "3D ↕"}.get(tags.stereo)) if x)


class VideosPage(Page):
    def __init__(self, host):
        super().__init__(host)
        self.place = RECENT            # RECENT or a folder Path
        self.page = 0
        self.dirs, self.videos = [], []
        self.loading = False
        self.drives = files.drives()
        self._load()

    # -- what is shown -------------------------------------------------------------------------------
    def _load(self):
        """Read the current place in the background (a big folder or a slow drive must not stall VR)."""
        place = self.place
        self.loading, self.page = True, 0

        def read():
            if place == RECENT:
                dirs, videos = [], files.recent_videos()
            else:
                dirs, videos = files.folder_entries(place)
            if place == self.place:
                self.dirs, self.videos, self.loading = dirs, videos, False

        threading.Thread(target=read, daemon=True, name="videos-list").start()

    def go(self, place):
        self.place = place
        self.dirs, self.videos = [], []
        self._load()

    def _private(self):
        return self.host.settings().private_mode

    def state(self):
        return (str(self.place), self.page, self.loading, len(self.dirs), len(self.videos), self._private())

    # -- the page ------------------------------------------------------------------------------------
    def build(self):
        w, h = PAGE_WIDTH, theme.ROW
        private = self._private()
        places = [Button("place:recent", "Recent", Icon.VIDEO, h=h, style="flat", selected=self.place == RECENT,
                         on_click=lambda: self.go(RECENT))]
        for d in self.drives:
            here = self.place != RECENT and Path(self.place).anchor.lower() == d.lower()
            places.append(Button("drive:" + d, d.rstrip("\\"), Icon.FOLDER, h=h, style="flat", selected=here,
                                 on_click=lambda d=d: self.go(Path(d))))
        rows = [Row([Label("Videos", size=theme.TITLE, color=theme.ACCENT, indent=24, h=h), Spacer(flex=1), *places],
                    w=w, h=int(h * 1.2), gap=6)]

        if self.place == RECENT:
            where = "Videos you opened lately, and the newest in Downloads, Videos and Desktop"
            up = None
        else:
            where = str(self.place)
            parent = Path(self.place).parent
            up = Button("up", "Up", Icon.UP, h=h, style="flat",
                        on_click=(lambda: self.go(parent)) if parent != Path(self.place) else None)
        rows.append(Row(([up] if up else []) + [Label(where, size=theme.SMALL, color=theme.MUTED, flex=1, h=h,
                                                      indent=12)], w=w, h=h, gap=6))

        if self.place == RECENT and private:
            rows.append(Label("Recent is hidden in private mode - open a drive above", size=theme.SMALL,
                              color=theme.MUTED, w=w, h=h * 2, align="center"))
            return Column(rows, pad=16, gap=6, bg=theme.BG, radius=28)

        items = [("dir", p) for p in self.dirs] + [("video", p) for p in self.videos]
        pages = max(1, (len(items) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
        self.page = min(self.page, pages - 1)
        shown = items[self.page * ROWS_PER_PAGE:(self.page + 1) * ROWS_PER_PAGE]
        row_h = int(h * 1.15)
        if self.loading:
            rows.append(Label("Looking...", size=theme.SMALL, color=theme.MUTED, w=w, h=row_h, indent=24))
        elif not shown:
            rows.append(Label("No videos here", size=theme.SMALL, color=theme.MUTED, w=w, h=row_h, indent=24))
        for i, (kind, path) in enumerate(shown):
            key = f"item:{self.page}:{i}"
            if kind == "dir":
                rows.append(Button(key, path.name, Icon.FOLDER, w=w, h=row_h, style="flat",
                                   on_click=lambda p=path: self.go(p)))
            else:
                label = path.name if self.place != RECENT else f"{path.name}   ·   {path.parent}"
                badge = _kind(path)
                rows.append(Row([Button(key, label, Icon.PLAY, h=row_h, flex=1, style="flat",
                                        on_click=lambda p=path: self.host.play_file(p),
                                        on_right=lambda p=path: self.host.video_file_menu(p)),
                                 Label(badge, size=theme.SMALL, color=theme.ACCENT_LIGHT, h=row_h)],
                                w=w, h=row_h, gap=12))
        rows.append(Spacer(h=(ROWS_PER_PAGE - len(shown)) * (row_h + 6)))   # same height on every page
        if pages == 1:
            rows.append(Spacer(h=h))   # the page buttons' room: the window keeps its shape
        else:
            rows.append(Row([Spacer(flex=1),
                             Button("page:-1", icon=Icon.PAGE_UP, w=h + 20, h=h, style="flat",
                                    on_click=lambda: self._turn(-1, pages)),
                             Label(f"{self.page + 1} / {pages}", size=theme.SMALL, color=theme.MUTED, h=h),
                             Button("page:1", icon=Icon.PAGE_DOWN, w=h + 20, h=h, style="flat",
                                    on_click=lambda: self._turn(1, pages))], w=w, h=h, gap=12))
        return Column(rows, pad=16, gap=6, bg=theme.BG, radius=28)

    def _turn(self, step, pages):
        self.page = max(0, min(pages - 1, self.page + step))


class VideosApp(ViewContent):
    icon = Icon.VIDEO

    def __init__(self, panel, host):
        super().__init__(panel, host, VideosPage(host))
