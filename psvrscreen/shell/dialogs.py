"""Dialogs opened from the dock: settings for everything, and lists to pick from (Add window, Apps).
They appear at eye level in front of you, closer than everything else. And the right-click menu."""
from ..core.settings import LIMITS
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Label, Row, Slider, Spacer, Toggle

DIALOG_DISTANCE = 1.0
MENU_DISTANCE = 0.9               # the right-click menu opens this far away, where you pointed
MENU_WIDTH = 620
SETTINGS_WIDTH = 1100
LIST_WIDTH = 1100
LIST_PAGE_SIZE = 10

# (kind, field, label, format)
GLOBAL_SETTINGS_ROWS = [
    ("title", None, "Mouse & controller", None),
    ("slider", "mouse_speed", "Mouse speed", "{:.1f}x"),
    ("toggle", "auto_add_windows", "Apps opened from VR open in VR", None),
    ("title", None, "Privacy", None),
    ("toggle", "private_mode", "Private mode: hide titles, no history", None),
    ("title", None, "Picture", None),
    ("slider", "lens_fov", "Field of view", "{:.0f}°"),
    ("slider", "ipd", "Eye distance", "{:.3f} m"),
    ("title", None, "Line up your eyes (turn the test on: make the red and green cross one)", None),
    ("toggle", "eye_test", "Eye alignment test", None),
    ("slider", "lens_apart", "Eyes apart", "{:+.3f}"),
    ("slider", "lens_height", "Eye height", "{:+.3f}"),
    ("title", None, "Layout", None),
    ("toggle", "follow_head", "Windows follow my head", None),
]


class GlobalSettings(View):
    def __init__(self, shell):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell = shell

    def state(self):
        s = self.shell.settings()
        return tuple(getattr(s, key) for kind, key, *_ in GLOBAL_SETTINGS_ROWS if key)

    def build(self):
        s, store = self.shell.settings(), self.shell.store
        rows = [Label("Settings", size=theme.TITLE, w=SETTINGS_WIDTH, h=int(theme.ROW * 1.2), indent=24)]
        for kind, key, label, fmt in GLOBAL_SETTINGS_ROWS:
            if kind == "title":
                rows.append(Label(label, size=theme.SMALL, color=theme.ACCENT, w=SETTINGS_WIDTH, indent=24))
            elif kind == "slider":
                lo, hi = LIMITS[key]
                rows.append(Slider(key, label, getattr(s, key), lo, hi, fmt, w=SETTINGS_WIDTH,
                                   on_change=lambda v, k=key: store.update(**{k: v})))
            else:
                rows.append(Toggle(key, label, getattr(s, key), w=SETTINGS_WIDTH,
                                   on_change=lambda v, k=key: store.update(**{k: v})))
        return Column(rows, pad=12, bg=theme.BG, radius=theme.RADIUS)


class ListDialog(View):
    """A list to pick from, in pages. entries() -> [(label, payload)]; on_pick(payload)."""

    def __init__(self, title, entries, on_pick, empty_text):
        super().__init__(distance=DIALOG_DISTANCE)
        self.title, self.entries, self.on_pick, self.empty_text = title, entries, on_pick, empty_text
        self.page = 0

    def state(self):
        return tuple(label for label, _ in self.entries()), self.page

    def _turn(self, step, pages):
        self.page = max(0, min(pages - 1, self.page + step))

    def build(self):
        entries = self.entries()
        pages = max(1, (len(entries) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
        self.page = min(self.page, pages - 1)
        shown = entries[self.page * LIST_PAGE_SIZE:(self.page + 1) * LIST_PAGE_SIZE]
        row_h = int(theme.TEXT * 1.7)
        head = [Label(self.title, color=theme.ACCENT, h=row_h, flex=1, indent=24)]
        if pages > 1:
            head += [Label(f"{self.page + 1} / {pages}", size=theme.SMALL, color=theme.MUTED, h=row_h),
                     Button("page:-1", icon=Icon.PAGE_UP, w=row_h, h=row_h - 12, style="flat",
                            on_click=lambda: self._turn(-1, pages)),
                     Button("page:1", icon=Icon.PAGE_DOWN, w=row_h, h=row_h - 12, style="flat",
                            on_click=lambda: self._turn(1, pages))]
        rows = [Row(head, w=LIST_WIDTH, h=row_h, gap=8)]
        if not shown:
            rows.append(Label(self.empty_text, size=theme.TEXT - 2, color=theme.MUTED, w=LIST_WIDTH, h=row_h,
                              indent=24))
        for i, (label, payload) in enumerate(shown):
            rows.append(Button(f"item:{self.page}:{i}", label, w=LIST_WIDTH - 24, h=row_h - 6, size=theme.TEXT - 2,
                               style="flat", on_click=lambda p=payload: self.on_pick(p)))
        if pages > 1:   # same height on every page
            rows.append(Spacer(h=(LIST_PAGE_SIZE - len(shown)) * (row_h - 6 + 4)))
        return Column(rows, pad=12, gap=4, bg=theme.BG, radius=theme.RADIUS, w=LIST_WIDTH + 24)


class ContextMenu(View):
    """Right click: the options for what you pointed at. items: [(label, icon, action, style)]; picking one
    closes the menu, then runs it. A click anywhere else closes it."""

    def __init__(self, title, items, close):
        super().__init__(distance=MENU_DISTANCE)
        self.title, self.items, self.close = title, items, close

    def _run(self, action):
        self.close()
        action()

    def build(self):
        h = theme.ROW
        rows = [Label(self.title, size=theme.SMALL, color=theme.MUTED, w=MENU_WIDTH, h=int(h * 0.8), indent=20)]
        for i, (label, icon, action, style) in enumerate(self.items):
            rows.append(Button(f"menu:{i}", label, icon, w=MENU_WIDTH, h=h, size=theme.TEXT - 4, style=style,
                               on_click=lambda a=action: self._run(a)))
        return Column(rows, pad=10, gap=2, bg=theme.BG, radius=theme.RADIUS)
