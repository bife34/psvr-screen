"""Files: your folders and drives in VR - a VR desktop of folders, not a window of arrows.

    ┌ Places ──────┐  Home › C: › Users › You › Downloads               ▦ Grid  ≡ List   Name ▾   ⧉ New window
    │ ⌂ Home       │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                                                    ▐
    │ ↓ Downloads  │  │ 📁   │ │ 🖼   │ │ ▶    │ │ PDF  │   cards: pictures / videos show themselves,          ▐
    │ ...          │  └──────┘ └──────┘ └──────┘ └──────┘   every file its kind (in colour), size and date     ▐
    │ Drives       │  ...                                                                                   ▐
    │ C: 120 GB fr │                                                                                        ▐
    │ Pinned       │  wheel / right stick: scroll (no page arrows), the bar on the right shows where you are
    └──────────────┘

The path is a row of chips - press any part to go there. Every folder can be its own window ("⧉ New window",
or right click > Open in its own window): put Downloads on your left, a project in front, photos behind - each
window keeps its folder, like folders on a desk. Right click a folder: pin it to Places, photo wall, Explorer.
Videos play around you, pictures open in the picture viewer (with "View around me"), anything else opens in its
Windows app - and that app's window comes into VR.
"""
import shutil
import threading
import time
from pathlib import Path

from ..ui import theme
from ..ui.theme import Icon
from ..core import geometry
from ..scene.surface import INTERFACE, Surface
from ..ui.view import View
from ..ui.widgets import Button, Card, Column, Grid, Label, Panel, Row, Spacer, Widget
from ..video import files
from ..video.files import as_path, in_vault
from ..video.thumbs import THUMBNAILS
from .base import ViewContent
from .photos import PhotosPage
from .photos import _kind as picture_kind
from .videos import PAGE_WIDTH, RECENT, _duration
from .videos import _kind as video_kind

MUSIC_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"}
PROGRAM_EXTENSIONS = {".exe", ".lnk", ".url", ".bat", ".msi"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt",
                       ".pptx", ".json", ".xml", ".html", ".py", ".js"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"}
KIND_COLORS = {   # a file's kind, in colour on its card
    "folder": (235, 170, 60, 255), "picture": (80, 190, 120, 255), "video": (175, 82, 222, 255),
    "music": (236, 92, 140, 255), "document": (79, 156, 249, 255), "archive": (160, 120, 80, 255),
    "program": (40, 180, 180, 255), "file": (99, 99, 110, 255),
}
SIDEBAR_W = 360
LAYER_PAGE_H = 1078                # a folder behind: as tall as the page, so it lines up
DEFAULT_FRAME = (PAGE_WIDTH, LAYER_PAGE_H)
MIN_FRAME = (1600, 900)            # a window can't get smaller than this (the places, toolbar and 2 columns fit)
MAX_FRAME = (5200, 3200)
CHROME_H = 2 * theme.ROW + 4 + (theme.ROW - 16) + 2 * theme.GAP   # path, toolbar, hint: above / below the cards
LINE_H = 58                        # a line in the list
PEEK_ITEMS = 6
PEEK_CARD_W, PEEK_CARD_H = 210, 170
COLUMNS, ROWS_SHOWN = 4, 3
LIST_ROWS = 9
GAP = theme.GAP * 2
MAIN_W = PAGE_WIDTH - 64 - SIDEBAR_W - GAP
CARD_W = (MAIN_W - 24 - (COLUMNS - 1) * GAP) // COLUMNS
CARD_H = int(CARD_W * 9 / 16) + int(theme.SMALL * 2.6)
DRAG_DEGREES = 2.0                 # a press that moves more than this drags the file
PEEK_SECONDS = 0.6                 # pointing at a folder this long shows what's in it
LAYERS = 3                         # folders shown behind: the ones this one is in
LAYER_DEPTH = 0.1                  # m each one further back (close: a subtle stack, not a corridor)
LAYER_RISE = 0.085                 # ...and this part of the window's height higher (their names show above it)
LAYER_NAME_H = 64                  # px: a folder's name at the very top of its layer
FLIGHT_SECONDS = 0.35              # going into / out of a folder: the flight through the layers
FLIGHT_IN_FROM = 0.12              # m a folder you go into starts in front of its place
SORTS = (("name", "Name"), ("date", "Newest"), ("size", "Biggest"), ("kind", "Kind"))
PLACE_NAMES = (("Desktop", Icon.HOME), ("Downloads", Icon.PAGE_DOWN), ("Documents", Icon.FILE),
               ("Pictures", Icon.PHOTO), ("Videos", Icon.VIDEO), ("Music", Icon.MUSIC))


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def kind_of(path, is_dir=False):
    if is_dir:
        return "folder"
    ext = path.suffix.lower()
    if ext in files.EXTENSIONS:
        return "video"
    if ext in files.PICTURE_EXTENSIONS:
        return "picture"
    if ext in MUSIC_EXTENSIONS:
        return "music"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    if ext in PROGRAM_EXTENSIONS:
        return "program"
    return "file"


KIND_ICONS = {"folder": Icon.FOLDER, "video": Icon.VIDEO, "picture": Icon.PHOTO, "music": Icon.MUSIC,
              "document": Icon.FILE, "archive": Icon.FILE, "program": Icon.APPS, "file": Icon.FILE}


class ScrollBar(Widget):
    """Where you are in a long folder: a thin track with the part you see."""

    def __init__(self, first, shown, total, h):
        super().__init__(10, h)
        self.first, self.shown, self.total = first, shown, total

    def draw(self, d, ctx):
        x0, y0, x1, y1 = (int(v) for v in self.box)
        if self.total <= self.shown:
            return
        d.rounded_rectangle((x0, y0, x1, y1), 5, fill=(255, 255, 255, 25))
        top = y0 + (y1 - y0) * self.first / self.total
        bottom = y0 + (y1 - y0) * min(1.0, (self.first + self.shown) / self.total)
        d.rounded_rectangle((x0, int(top), x1, max(int(top) + 24, int(bottom))), 5, fill=theme.ACCENT)


class FilesPage(PhotosPage):
    TITLE, ICON, EMPTY = "Files", Icon.FOLDER, "This folder is empty"
    COLOR = KIND_COLORS["folder"]
    APP = "files"
    RECENT_LABEL, RECENT_ICON = "Home", Icon.HOME
    RECENT_HINT = "Your folders"
    RECENT_IS_PRIVATE = False
    BACK_LABEL = "Back to the folder"

    def __init__(self, host, window_id=None, place=None):
        self.window_id = window_id
        self.scroll = 0                       # first row shown
        self._paths = {}                      # card / line key -> its file or folder (this frame's page)
        self._grabbed = None                  # (path, yaw, pitch) pressed on, not dragged yet
        self.dragging = None                  # the file being dragged out of this window
        self.flight = (-10.0, "in")           # (when, "in" / "out"): the last move into / out of a folder
        self._peek = (None, 0.0)              # (folder card pointed at, since when)
        self.frame = DEFAULT_FRAME            # the page's size in pixels: its window's (resize corner)
        self.free = {}                        # drive -> free bytes (read in the background)
        super().__init__(host)
        threading.Thread(target=self._read_drives, daemon=True, name="files-drives").start()
        self._pending = None                  # a place in the room's safe, until the safe is open
        if place and str(place).startswith("vault:"):
            self._pending = str(place)
        elif place and Path(place).is_dir():
            self.go(Path(place))

    def _read_drives(self):
        for d in self.drives:
            try:
                self.free[d] = shutil.disk_usage(d).free
            except OSError:
                pass

    # -- the library's choices -----------------------------------------------------------------------------
    def place_name(self):
        return self.RECENT_LABEL, self.RECENT_ICON, self.RECENT_HINT

    def first_place(self):
        return files.home_folders(), []

    def folder(self, path):
        return files.folder_entries(path, None)

    def go(self, place):
        old = self.place
        self.viewing, self.scroll, self._peek = None, 0, (None, 0.0)
        try:
            deeper = place != RECENT and (old == RECENT or as_path(place).is_relative_to(as_path(old)))
        except TypeError:   # (from a folder into the safe or back)
            deeper = in_vault(place)
        self.flight = (time.monotonic(), "in" if deeper else "out")   # the folders fly in / out of depth
        super().go(place)
        if self.window_id:   # each Files window keeps its own folder (and shows it on its tab)
            name = place_name(place)
            self.host.update_panel(self.window_id, place="" if place == RECENT else str(place),
                                   title=f"{self.TITLE} · {name}")

    def extra_tools(self, h):
        """More toolbar buttons (Photos: Photo wall, Rooms)."""
        return []

    def go_up(self):
        """Back out of this folder: to the one it's in (a drive: to Home)."""
        if self.place == RECENT:
            return
        here = as_path(self.place)
        self.go(RECENT if here.parent == here else here.parent)

    def resolve_pending(self):
        """A window saved inside the safe: once the safe is open, back to its folder."""
        safe = self.host.room_safe()
        if self._pending and safe is not None:
            parts = [p for p in self._pending[len("vault:"):].split("/") if p]
            self._pending = None
            self.go(type(safe.root)(safe, parts))

    def peeked(self, now):
        """The folder card pointed at long enough to peek into: (path, its card's box), or None."""
        key = self.ctx.hover
        path = self._paths.get(key)
        if path is None or not path.is_dir() or self.viewing is not None:
            self._peek = (None, 0.0)
            return None
        if self._peek[0] != key:
            self._peek = (key, now)
            return None
        widget = self._by_key(key)
        return (path, widget.box) if widget is not None and now - self._peek[1] > PEEK_SECONDS else None

    def open(self, path):
        ext = path.suffix.lower()
        if ext in files.PICTURE_EXTENSIONS:
            super().open(path)
        elif ext in files.EXTENSIONS:
            self.host.play_file(path)
        elif in_vault(path):   # Windows' apps would need a readable copy on disk
            self.host.toast("Take it out of the safe to open it in Windows (right click > Take out)")
        else:
            self.host.open_file(path)

    def menu(self, path):
        self.host.file_menu(path)

    # -- input: the wheel scrolls (no page arrows) -------------------------------------------------------------
    def on_pointer(self, e):
        if e.kind == "wheel" and self.viewing is None:   # the wheel only scrolls (going back: the folders behind)
            self.scroll = max(0, self.scroll + (1 if e.delta < 0 else -1))
            return
        if e.kind == "press" and e.button == "left" and self.viewing is None:
            w = self.widget_at(e.u, e.v)
            path = self._paths.get(w.key) if w else None
            self._grabbed = (path, e.yaw, e.pitch) if path else None
        elif e.kind == "drag" and self._grabbed and not self.dragging:
            path, yaw, pitch = self._grabbed
            if abs(e.yaw - yaw) + abs(e.pitch - pitch) > DRAG_DEGREES:   # pulled: it's a drag, not a click
                self.dragging, self.ctx.press = path, None
                self.host.start_file_drag(path, self.window_id)
                return
        elif e.kind == "release":
            self._grabbed = None
            if self.dragging:                 # the shell drops it where the pointer is
                self.dragging = None
                return
        super().on_pointer(e)

    def state(self):
        s = self.host.settings()
        return super().state() + (self.scroll, s.files_view, s.files_sort, tuple(s.pinned_folders),
                                  tuple(sorted(self.free.items())))

    # -- the page ----------------------------------------------------------------------------------------
    def build(self):
        if self.viewing is not None and self.viewing < len(self.pictures()):
            return super().build()                 # the picture viewer (Photos)
        s = self.host.settings()
        side = self._sidebar(s)
        main = self._main(s)
        fw, fh = self.frame
        return Panel([Row([side, main], gap=GAP, align="start")], pad=32, w=fw, h=fh)

    def _sidebar(self, s):
        h = theme.ROW - 6

        def place(key, text, icon, target, selected):
            return Button(key, text, icon, w=SIDEBAR_W, h=h, style="flat", selected=selected, size=theme.TEXT - 8,
                          on_click=lambda: self.go(target))

        here = None if self.place == RECENT else as_path(self.place)
        rows = [Row([Label(self.ICON, size=theme.TITLE, color=self.COLOR, icon=True, w=64,
                           h=int(theme.ROW * 1.3)),
                     Label(self.TITLE, size=theme.HUGE - 16, h=int(theme.ROW * 1.3))], w=SIDEBAR_W),
                place("side:home", "Home", Icon.HOME, RECENT, self.place == RECENT)]
        homes = {f.name: f for f in files.home_folders()}
        for name, icon in PLACE_NAMES:
            folder = next((f for n, f in homes.items() if n == name or _same_place(n, name)), None)
            if folder is not None:
                rows.append(place("side:" + name, folder.name, icon, folder, here == folder))
        safe = self.host.room_safe()
        if safe is not None:   # in a locked room: its safe (encrypted files)
            rows.append(place("side:safe", "Safe", Icon.LOCKED, safe.root,
                              in_vault(here) and here.vault is safe and not here.parts))
        rows.append(Label("Drives", size=theme.SMALL - 6, color=theme.MUTED, w=SIDEBAR_W, h=int(h * 0.8), indent=16))
        for d in self.drives:
            free = self.free.get(d)
            text = d.rstrip("\\") + (f"   {_size(free)} free" if free is not None else "")
            rows.append(place("side:" + d, text, Icon.DRIVE, Path(d),
                              here is not None and here.anchor.lower() == d.lower() and len(here.parts) == 1))
        if s.pinned_folders:
            rows.append(Label("Pinned", size=theme.SMALL - 6, color=theme.MUTED, w=SIDEBAR_W, h=int(h * 0.8),
                              indent=16))
            for p in s.pinned_folders:
                path = Path(p)
                b = place("pin:" + p, path.name or p, Icon.PIN, path, here == path)
                b.on_right = lambda p=p: self.host.folder_menu(Path(p), self.window_id)
                rows.append(b)
        return Column(rows, gap=2, w=SIDEBAR_W, align="start")

    def _crumbs(self, w):
        """Home › C: › Users › ... : every part is a button."""
        h = theme.ROW - 10
        chips = [Button("crumb:home", "Home", Icon.HOME, h=h, style="flat", size=theme.TEXT - 8,
                        on_click=lambda: self.go(RECENT))]
        if self.place != RECENT:
            path = as_path(self.place)
            if in_vault(path):
                parts = [type(path)(path.vault, path.parts[:i]) for i in range(len(path.parts) + 1)]
            else:
                parts = [Path(*path.parts[:i + 1]) for i in range(len(path.parts))]
            if len(parts) > 3:
                parts = parts[:1] + [None] + parts[-2:]
            for i, part in enumerate(parts):
                chips.append(Label("›", size=theme.TEXT - 8, color=theme.MUTED, h=h, w=26, align="center"))
                if part is None:
                    chips.append(Label("…", size=theme.TEXT - 8, color=theme.MUTED, h=h, w=30, align="center"))
                    continue
                name = place_name(part).rstrip("\\")
                name = name if len(name) <= 22 else name[:20] + "…"
                chips.append(Button(f"crumb:{i}", name, h=h, style="flat", size=theme.TEXT - 8,
                                    selected=part == path, on_click=lambda part=part: self.go(part)))
        return Row(chips, h=theme.ROW, gap=2)

    def _toolbar(self, s, w):
        h = theme.ROW - 14
        view = [Button("view:" + key, None, icon, w=h + 12, h=h, style="flat", selected=s.files_view == key,
                       size=theme.TEXT - 8, on_click=lambda key=key: self.host.set_setting("files_view", key))
                for key, icon in (("grid", Icon.GALLERY), ("list", Icon.APPS))]
        names = dict(SORTS)
        keys = [k for k, _ in SORTS]
        following = keys[(keys.index(s.files_sort) + 1) % len(keys)] if s.files_sort in keys else "name"
        sort = Button("sort", names.get(s.files_sort, "Name"), Icon.CHEVRON, h=h, style="normal",
                      size=theme.TEXT - 8, on_click=lambda: self.host.set_setting("files_sort", following))
        items = view + [sort]
        items += self.extra_tools(h)
        if self.place != RECENT:   # the rest (own window, pin, Explorer...): the folder's action ring
            place = as_path(self.place)
            items.append(Button("more", "More", Icon.CHEVRON, h=h, style="normal", size=theme.TEXT - 8,
                                on_click=lambda: self.host.folder_menu(place, self.window_id)))
        return Row(items, h=theme.ROW, gap=6)

    def _entries(self, s):
        entries = [(p, True) for p in self.dirs] + [(p, False) for p in self.items]
        if s.files_sort == "date":
            entries.sort(key=lambda e: _stat(e[0]).st_mtime if _stat(e[0]) else 0, reverse=True)
        elif s.files_sort == "size":
            entries.sort(key=lambda e: (not e[1], -(_stat(e[0]).st_size if _stat(e[0]) and not e[1] else 0)))
        elif s.files_sort == "kind":
            entries.sort(key=lambda e: (kind_of(e[0], e[1]) != "folder", kind_of(e[0], e[1]), e[0].name.lower()))
        return entries

    def _main(self, s):
        fw, fh = self.frame
        w = fw - 64 - SIDEBAR_W - GAP
        list_view = s.files_view == "list"
        columns = max(1, (w - 24 + GAP) // (CARD_W + GAP))
        body_h = fh - 64 - CHROME_H                      # the window's room left for the cards / lines
        per_row = 1 if list_view else columns
        shown_rows = (max(1, (body_h + 4) // (LINE_H + 4)) if list_view
                      else max(1, (body_h + GAP) // (CARD_H + GAP)))
        entries = self._entries(s)
        total_rows = max(1, -(-len(entries) // per_row))
        self.scroll = max(0, min(self.scroll, total_rows - shown_rows))
        first = self.scroll * per_row
        visible = entries[first:first + shown_rows * per_row]
        prefix = "line" if list_view else "card"
        self._paths = {f"{prefix}:{first + i}": p for i, (p, _) in enumerate(visible)}
        count = f"{len(self.dirs)} folders · {len(self.items)} files" if entries else ""
        header = Column([Row([self._crumbs(w)], w=w, h=theme.ROW),
                         Row([Label(count, size=theme.SMALL - 8, color=theme.MUTED, h=theme.ROW - 14, indent=12),
                              Spacer(flex=1), self._toolbar(s, w)], w=w, h=theme.ROW)], gap=4, w=w)
        if self.loading:
            body = Label("Looking...", size=theme.SMALL, color=theme.MUTED, w=w - 24, h=body_h, align="center")
        elif not entries:
            body = Label(self.EMPTY, size=theme.SMALL, color=theme.MUTED, w=w - 24, h=body_h, align="center")
        elif list_view:
            row_h = LINE_H
            body = Column([self._line(f"line:{first + i}", p, d, w - 24, row_h) for i, (p, d) in enumerate(visible)],
                          gap=4, w=w - 24, h=body_h, align="start")
        else:
            body = Row([Grid([self._card(f"card:{first + i}", p, d) for i, (p, d) in enumerate(visible)], columns)],
                       w=w - 24, h=body_h, align="start")
        hint = "Back: press a folder above the window, or the path" if self.place != RECENT else ""
        footer = Row([Spacer(flex=1), Label(hint, size=theme.SMALL - 8, color=theme.MUTED, h=theme.ROW - 16)],
                     w=w, h=theme.ROW - 16)
        return Column([header, Row([body, Spacer(w=14), ScrollBar(self.scroll, shown_rows, total_rows, body_h)],
                                   w=w, h=body_h, align="start"), footer], gap=theme.GAP, w=w)

    def _card(self, key, path, is_dir):
        kind = kind_of(path, is_dir)
        if is_dir:
            return Card(key, path.name, None, CARD_W, CARD_H, icon=Icon.FOLDER,
                        badges=[("Folder", KIND_COLORS["folder"])],
                        on_click=lambda: self.go(path), on_right=lambda: self.host.folder_menu(path, self.window_id))
        image, seconds = None, None
        if kind in ("video", "picture"):
            image, seconds = THUMBNAILS.get(path)
        stat = _stat(path)
        subtitle = " · ".join(x for x in (_duration(seconds), _size(stat.st_size) if stat else "",
                                          time.strftime("%d %b %Y", time.localtime(stat.st_mtime)) if stat else "")
                              if x)
        ext = path.suffix[1:].upper()
        media = (video_kind(path) if kind == "video" else picture_kind(path) if kind == "picture" else [])
        return Card(key, path.name, image, CARD_W, CARD_H, badges=([(ext, KIND_COLORS[kind])] if ext else []) + media,
                    icon=KIND_ICONS[kind], subtitle=subtitle or None, on_click=lambda: self.open(path),
                    on_right=lambda: self.menu(path))

    def _line(self, key, path, is_dir, w, h):
        kind = kind_of(path, is_dir)
        stat = _stat(path)
        when = time.strftime("%d %b %Y  %H:%M", time.localtime(stat.st_mtime)) if stat else ""
        size = "" if is_dir or not stat else _size(stat.st_size)
        row = Row([Label(KIND_ICONS[kind], size=theme.TEXT - 6, color=KIND_COLORS[kind], icon=True, w=60, h=h,
                         align="center"),
                   Label(path.name, size=theme.TEXT - 8, flex=1, h=h),
                   Label(when, size=theme.SMALL - 8, color=theme.MUTED, w=300, h=h),
                   Label(size, size=theme.SMALL - 8, color=theme.MUTED, w=160, h=h, align="right")],
                  w=w, h=h, gap=8)
        return _LineButton(key, row, w, h, on_click=(lambda: self.go(path)) if is_dir else (lambda: self.open(path)),
                           on_right=(lambda: self.host.folder_menu(path, self.window_id)) if is_dir
                           else (lambda: self.menu(path)))


class _LineButton(Widget):
    """One line of the list: hover lights it up, click opens, right click: its menu."""
    interactive = True

    def __init__(self, key, row, w, h, on_click=None, on_right=None):
        super().__init__(w, h, key)
        self.row, self.on_click, self.on_right = row, on_click, on_right

    def layout(self, x, y, w, h):
        super().layout(x, y, w, h)
        self.row.layout(x, y, w, h)

    def draw(self, d, ctx):
        if ctx.hovered(self.key) or ctx.pressed(self.key):
            x0, y0, x1, y1 = (int(v) for v in self.box)
            d.rounded_rectangle((x0, y0, x1, y1), 10, fill=theme.HOVER)
        self.row.draw(d, ctx)

    def press(self, ctx, e):
        pass

    def release(self, ctx, e, inside=True):
        if not inside:
            return
        if e.button == "right" and self.on_right:
            self.on_right()
        elif e.button == "left" and self.on_click:
            self.on_click()


def place_name(place):
    """A place's name as shown: Home, the safe, a folder, a drive."""
    if place == RECENT:
        return "Home"
    if in_vault(place):
        return place.name or "Safe"
    return Path(place).name or str(place)


def _stat(path):
    try:
        return path.stat()
    except OSError:
        return None


def _same_place(folder_name, english):
    return folder_name in {"Desktop": ("Ambiente de Trabalho",), "Downloads": ("Transferências",),
                           "Documents": ("Documentos",), "Pictures": ("Imagens",), "Videos": ("Vídeos",),
                           "Music": ("Música",)}.get(english, ())


class FolderLayer(View):
    """A folder this one is in, drawn behind the window: its name big at the top (that's what shows above the
    window), its first folders and files below. Press it to go back there."""

    def __init__(self, path, frame=None):
        super().__init__(m_per_px=1.0)
        self.path, self.entries = path, None
        self.frame = frame or DEFAULT_FRAME
        threading.Thread(target=self._read, daemon=True, name="files-layer").start()

    def _read(self):
        if self.path == RECENT:
            self.entries = [(p, True) for p in files.home_folders()]
            return
        dirs, items = files.folder_entries(self.path, None)
        self.entries = [(p, True) for p in dirs[:12]] + [(p, False) for p in items[:12 - min(12, len(dirs))]]

    def state(self):
        return self.entries is not None

    def build(self):
        w = PAGE_WIDTH - 64
        name = place_name(self.path)
        tiles = [Row([Label(KIND_ICONS[kind_of(p, d)], size=theme.TEXT - 6, color=KIND_COLORS[kind_of(p, d)],
                            icon=True, w=56, h=theme.ROW),
                      Label(p.name, size=theme.SMALL - 6, h=theme.ROW, w=w // 4 - 80)], w=w // 4 - 12, h=theme.ROW,
                     bg=theme.CARD, radius=theme.RADIUS_CARD) for p, d in (self.entries or [])]
        return Panel([Row([Label(Icon.FOLDER, size=theme.TEXT, color=KIND_COLORS["folder"], icon=True, w=64,
                                 h=LAYER_NAME_H),
                           Label(name, size=theme.TEXT - 2, h=LAYER_NAME_H)], w=w),   # in the strip that shows
                      Grid(tiles, 4)], w=self.frame[0], h=self.frame[1], pad=10, gap=theme.GAP, align="start")


class FolderPeek(View):
    """What's in a folder you point at: its first pictures / folders / files, floating above it."""

    def __init__(self, path):
        super().__init__(m_per_px=1.0)
        self.path, self.entries, self.total = path, None, 0
        threading.Thread(target=self._read, daemon=True, name="files-peek").start()

    def _read(self):
        dirs, items = files.folder_entries(self.path, None)
        self.total = len(dirs) + len(items)
        self.entries = [(p, False) for p in items[:PEEK_ITEMS]] + [(p, True) for p in dirs[:PEEK_ITEMS - len(items)]]
        self.entries = self.entries[:PEEK_ITEMS]

    def state(self):
        return self.entries is not None, THUMBNAILS.version

    def build(self):
        cards = []
        for i, (p, d) in enumerate(self.entries or []):
            kind = kind_of(p, d)
            image = THUMBNAILS.get(p)[0] if kind in ("picture", "video") else None
            cards.append(Card(f"peek:{i}", p.name, image, PEEK_CARD_W, PEEK_CARD_H, icon=KIND_ICONS[kind]))
        if self.entries is None:
            body = [Label("Looking...", size=theme.SMALL - 6, color=theme.MUTED, h=PEEK_CARD_H, w=PEEK_CARD_W * 2)]
        elif not cards:
            body = [Label("Empty folder", size=theme.SMALL - 6, color=theme.MUTED, h=PEEK_CARD_H, w=PEEK_CARD_W * 2)]
        else:
            body = cards
        more = f"{self.total} items" if self.entries else ""
        return Panel([Row([Label(self.path.name, size=theme.SMALL - 4, h=theme.ROW - 20, indent=8), Spacer(flex=1),
                           Label(more, size=theme.SMALL - 8, color=theme.MUTED, h=theme.ROW - 20)],
                          w=max(1, len(body)) * (PEEK_CARD_W + theme.GAP * 2) - theme.GAP * 2),
                      Row(body, gap=theme.GAP * 2)], pad=18, gap=8)


class _LayerTarget:
    """Pointer on a folder behind the window: press it to go back there."""

    def __init__(self, page, place):
        self.page, self.place = page, place

    def on_pointer(self, e):
        if e.kind == "release" and e.button == "left":
            self.page.go(self.place)


def _ease(p):
    return 1 - (1 - p) ** 3


class PhotosBrowser(FilesPage):
    """Photos: the same browser, only pictures - click one: the picture viewer (View around me); Photo wall."""
    TITLE, ICON, EMPTY = "Photos", Icon.PHOTO, "No pictures here"
    COLOR = KIND_COLORS["picture"]
    APP = "photos"
    BACK_LABEL = "All photos"

    def first_place(self):
        return files.folders(PhotosPage.FOLDER_NAMES), []

    def folder(self, path):
        return files.folder_entries(path, files.PICTURE_EXTENSIONS)

    def menu(self, path):
        self.host.photo_menu(path, lambda: self.open(path))

    def extra_tools(self, h):
        tools = [Button("rooms", "Memories", Icon.STAR, h=h, style="flat", size=theme.TEXT - 8,
                        on_click=self.host.rooms_menu)]
        if self.place != RECENT and not in_vault(self.place):   # (a safe's photos: not on a wall yet)
            place = Path(self.place)
            tools.insert(0, Button("wall", "Photo wall", Icon.GALLERY, h=h, style="primary", size=theme.TEXT - 8,
                                   on_click=lambda: self.host.view_wall(place)))
        return tools


class VideosBrowser(FilesPage):
    """Videos: the same browser, only videos - click one: it plays around you (its kind recognised)."""
    TITLE, ICON, EMPTY = "Videos", Icon.VIDEO, "No videos here"
    COLOR = KIND_COLORS["video"]
    APP = "videos"

    def first_place(self):
        return files.folders(), []

    def folder(self, path):
        return files.folder_entries(path)

    def open(self, path):
        self.host.play_file(path)

    def menu(self, path):
        self.host.video_file_menu(path)


class FilesApp(ViewContent):
    icon = Icon.FOLDER
    PAGE = FilesPage

    def __init__(self, panel, host):
        super().__init__(panel, host, self.PAGE(host, panel["id"], panel.get("place") or None))
        self._layers = {}                 # place -> FolderLayer
        self._targets = {}                # place -> _LayerTarget (kept: the pointer knows them by them)
        self._peeks = {}                  # folder -> FolderPeek

    def on_pointer(self, e):
        self.view.on_pointer(e)

    resizable = True   # the resize corner gives the page more room (it reflows), instead of scaling it

    def update(self, panel, now):
        self.view.resolve_pending()
        frame = tuple(panel.get("frame") or DEFAULT_FRAME)
        if frame != self.view.frame:
            self.view.frame = frame
            self.view.invalidate()
        super().update(panel, now)

    def _progress(self, now):
        when, _ = self.view.flight
        return _ease(min(1.0, max(0.0, (now - when) / FLIGHT_SECONDS)))

    def offset(self, now):
        """The folder you go into comes from in front; the one you back out to, from behind."""
        p = self._progress(now)
        if p >= 1.0:
            return None
        _, way = self.view.flight
        return geometry.translate(0, 0, (1 - p) * (FLIGHT_IN_FROM if way == "in" else -LAYER_DEPTH))

    def _chain(self):
        page = self.view
        if page.place == RECENT or page.viewing is not None:
            return []
        here = as_path(page.place)
        chain = list(here.parents)[:LAYERS]
        if len(chain) < LAYERS:
            chain.append(RECENT)
        return chain

    def extra_surfaces(self, panel, model, height, now):
        out = []
        chain = self._chain()
        for place in [p for p in self._layers if p not in chain]:   # folders no longer behind: freed
            self._layers.pop(place).release()
            self._targets.pop(place, None)
        p = self._progress(now)
        _, way = self.view.flight
        for k, place in enumerate(chain, 1):
            layer = self._layers.get(place)
            if layer is not None and layer.frame != self.view.frame:   # the window was resized
                layer.release()
                layer = None
            if layer is None:
                layer = self._layers[place] = FolderLayer(place, self.view.frame)
                self._targets[place] = _LayerTarget(self.view, place)
            layer.refresh()
            depth = k - (1 - p) if way == "in" else k + (1 - p)      # flying back / forward into place
            opacity = max(0.0, min(1.0, 0.45 - 0.12 * depth)) * panel.get("opacity", 1.0)   # subtle
            if opacity <= 0.02:
                continue
            w = panel["width"]
            h = w * layer.size_px[1] / layer.size_px[0]
            m = model @ geometry.translate(0, depth * LAYER_RISE * height + (h - height) / 2, -depth * LAYER_DEPTH)
            out.append(Surface(f"files-layer:{self.window_id}:{place}", m, w, h, panel["curvature"],
                               texture=layer.texture, opacity=opacity, target=self._targets[place]))
        out += self._peek_surface(panel, model, height, now)
        return out

    def _peek_surface(self, panel, model, height, now):
        found = self.view.peeked(now)
        for path in [p for p in self._peeks if not found or p != found[0]]:
            self._peeks.pop(path).release()
        if not found:
            return []
        path, (x0, y0, x1, y1) = found
        peek = self._peeks.get(path)
        if peek is None:
            peek = self._peeks[path] = FolderPeek(path)
        peek.refresh()
        page_w, page_h = self.view.size_px
        m_per_px = panel["width"] / page_w
        w, h = peek.size_px[0] * m_per_px, peek.size_px[1] * m_per_px
        x = ((x0 + x1) / 2 / page_w - 0.5) * panel["width"]
        x = max(-panel["width"] / 2 + w / 2, min(panel["width"] / 2 - w / 2, x))
        y = (0.5 - y0 / page_h) * height + h / 2 + 0.01
        return [Surface(f"files-peek:{self.window_id}", model @ geometry.translate(x, y, 0.06), w, h,
                        texture=peek.texture, layer=INTERFACE)]

    def release(self):
        for v in list(self._layers.values()) + list(self._peeks.values()):
            v.release()
        self._layers.clear()
        self._peeks.clear()
        super().release()


class PhotosApp(FilesApp):
    icon = Icon.PHOTO
    PAGE = PhotosBrowser


class VideosApp(FilesApp):
    icon = Icon.VIDEO
    PAGE = VideosBrowser
