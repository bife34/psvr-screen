"""Dialogs opened from the dock: settings for everything, and lists to pick from (Add window, Apps).
They appear at eye level in front of you, closer than everything else. And the right-click menu."""
import math

from ..core.settings import DEFAULT_SHORTCUTS, LIMITS, SHORTCUT_ACTIONS
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..render import environment
from ..ui.widgets import Button, Card, Column, Grid, Label, Panel, Row, Slider, Spacer, Toggle, Widget

DIALOG_DISTANCE = 1.0
MENU_DISTANCE = 0.9               # the right-click menu opens this far away, where you pointed
MENU_WIDTH = 620
SETTINGS_WIDTH = 1500
SETTINGS_HEIGHT = 640
LIST_WIDTH = 1100
LIST_PAGE_SIZE = 10

# Settings, by section: (kind, field, label, format); kind "title" / "slider" / "toggle" / "note"
SETTINGS_SECTIONS = [
    ("display", "Display & eyes", Icon.EYE, [
        ("slider", "lens_fov", "Field of view", "{:.0f}°"),
        ("slider", "ipd", "Eye distance", "{:.3f} m"),
        ("title", None, "Line up your eyes", None),
        ("note", None, "Turn the test on, then move the sliders until the red and green cross are one.", None),
        ("toggle", "eye_test", "Eye alignment test", None),
        ("slider", "lens_apart", "Eyes apart", "{:+.3f}"),
        ("slider", "lens_height", "Eye height", "{:+.3f}"),
    ]),
    ("picture", "Picture", Icon.MONITOR, [
        ("choice", "picture_quality", "Quality", [("normal", "Normal"), ("high", "High"), ("max", "Max")]),
        ("note", None, "Higher draws everything bigger before the lenses: smoother edges and text, more work for "
                       "the graphics card.", None),
        ("slider", "picture_smoothing", "Sharp - Smooth", "{:+.1f}"),
        ("note", None, "Smoother: windows and small text look less pixelated and shimmer less. Sharper: crisper "
                       "but more jagged.", None),
    ]),
    ("surroundings", "Surroundings", Icon.SPHERE, [
        ("environments", "environment", None, None),
        ("slider", "environment_brightness", "Brightness", "{:.0%}"),
        ("slider", "environment_turn", "Turn the scene", "{:+.0f}°"),
        ("toggle", "scene_grab", "Grab the scene to turn it (drag empty space)", None),
        ("note", None, "Off by default: a scene spinning around you makes many people dizzy.", None),
    ]),
    ("controls", "Controls", Icon.MOUSE, [
        ("slider", "mouse_speed", "Mouse speed", "{:.1f}x"),
        ("toggle", "windows_apps", "Windows programs in VR (experimental)", None),
        ("toggle", "auto_add_windows", "...programs opened from VR open in VR", None),
        ("toggle", "follow_head", "Windows follow my head", None),
        ("toggle", "voice_control", "Voice commands (offline)", None),
        ("toggle", "smart_assistant", "Smart assistant (local AI, 2.8 GB once)", None),
        ("note", None, "Press Talk (dock, photo wall, video bar, DS4 R1 or Ctrl+Alt+V) and say \"beach\", \"next\", "
                       "\"bigger\", \"exit\"... The mic is only on while you talk.", None),
    ]),
    ("keys", "Shortcuts", Icon.KEYBOARD, [
        ("title", None, "Keyboard shortcuts", None),
        ("note", None, "Click a shortcut, then press its new keys (with Ctrl / Alt / Shift). Esc cancels.", None),
        ("shortcuts", None, None, None),
    ]),
    ("ds4", "DS4 controller", Icon.GAMEPAD, [
        ("toggle", "gyro_pointer", "Aim by moving the DS4", None),
        ("slider", "gyro_speed", "Aiming speed", "{:.1f}x"),
        ("choice", "talk_button", "Talk button", [("none", "Off"), ("r1", "R1"), ("square", "Square"),
                                                  ("l3", "L3"), ("r3", "R3")]),
        ("choice", "laser_style", "Laser look", [("beam", "Full beam"), ("fade", "Fading"), ("dot", "Dot only")]),
        ("title", None, "Line it up", None),
        ("note", None, "Point the DS4 where you look and click its touchpad: the pointer jumps there, and that "
                       "tilt means \"there\" from now on. Do it again whenever it feels off.", None),
        ("note", None, "Put the DS4 down for a second after turning it on: it measures itself while still.", None),
    ]),
    ("privacy", "Privacy", Icon.PRIVATE, [
        ("toggle", "show_recent", "Show Recent and Continue watching", None),
        ("note", None, "Off: Videos and Photos open on your folders, Home shows nothing you watched, and no "
                       "places in videos are kept. On: right click > Remove from Recent, or Clear Recent.", None),
        ("toggle", "private_mode", "Private mode", None),
        ("note", None, "Titles become the app's name or \"Video\", the Videos app shows no Recent, and the PC "
                       "screen shows nothing about what you watch.", None),
    ]),
    ("about", "About", Icon.INFO, [
        ("note", None, "PSVR Screen - your PC in the PS4 VR headset, without SteamVR.", None),
        ("note", None, "Left click: use things. Right click: options. Drag empty space in a video to move it.", None),
    ]),
]
SECTION_WIDTH = 380


class GlobalSettings(View):
    """Settings: sections on the left (like the settings of a phone), the chosen one's settings on the right."""

    def __init__(self, shell):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell = shell
        self.section = SETTINGS_SECTIONS[0][0]
        self.recording = None        # the shortcut being changed: waiting for its new keys
        environment.warm_previews()

    def state(self):
        s = self.shell.settings()
        if self.recording is not None:
            self._take_keys()
        keys = [key for _, _, _, rows in SETTINGS_SECTIONS for kind, key, *_ in rows if key]
        return ((self.section, self.recording, tuple(sorted(s.shortcuts.items())))
                + tuple(getattr(s, key) for key in keys))

    def _take_keys(self):
        """While changing a shortcut: the keys you press now become it (Esc: keep the old one)."""
        from ..win import shortcuts
        text = shortcuts.pressed_now()
        if text is None:
            return
        if text != "Escape":
            keys = dict(self.shell.settings().shortcuts)
            keys[self.recording] = text
            self.shell.store.update(shortcuts=keys)
            self.shell.pointer.reload_shortcuts()
        self.recording = None

    def _record(self, action):
        self.recording = None if self.recording == action else action

    def _show(self, section):
        self.section = section

    def build(self):
        s, store = self.shell.settings(), self.shell.store
        w = SETTINGS_WIDTH - SECTION_WIDTH
        nav = [Label("Settings", size=theme.TITLE, w=SECTION_WIDTH, h=int(theme.ROW * 1.4), indent=12)]
        for key, name, icon, _ in SETTINGS_SECTIONS:
            nav.append(Button("section:" + key, name, icon, w=SECTION_WIDTH, h=theme.ROW - 6, size=theme.TEXT - 6,
                              style="flat", selected=key == self.section, on_click=lambda key=key: self._show(key)))
        rows = []
        for kind, key, label, fmt in next(r for k, _, _, r in SETTINGS_SECTIONS if k == self.section):
            if kind == "title":
                rows.append(Label(label, size=theme.SMALL, color=theme.ACCENT_LIGHT, w=w, indent=24))
            elif kind == "note":
                rows += [Label(line, size=theme.SMALL - 6, color=theme.MUTED, w=w, h=int(theme.SMALL * 1.2), indent=24)
                         for line in _wrap(label, theme.font(theme.SMALL - 6), w - 48)]
            elif kind == "shortcuts":
                for action, name in SHORTCUT_ACTIONS.items():
                    keys = s.shortcuts.get(action, DEFAULT_SHORTCUTS.get(action, ""))
                    recording = self.recording == action
                    rows.append(Row([Label(name, size=theme.SMALL - 4, flex=1, indent=24),
                                     Button("keys:" + action, "Press the new keys... (Esc: cancel)" if recording
                                            else keys, h=theme.ROW - 14, style="normal" if recording else "flat",
                                            selected=recording, size=theme.SMALL - 4,
                                            on_click=lambda a=action: self._record(a))], w=w, h=theme.ROW - 8))
            elif kind == "environments":
                rows.append(_environment_cards(s, store, w))
            elif kind == "choice":
                rows.append(Row([Label(label, size=theme.TEXT - 4, flex=1, indent=24)] +
                                [Button(f"choice:{key}:{value}", text, h=theme.ROW - 12, style="flat",
                                        selected=getattr(s, key) == value, size=theme.SMALL - 4,
                                        on_click=lambda k=key, value=value: store.update(**{k: value}))
                                 for value, text in fmt], w=w, h=theme.ROW, gap=6))
            elif kind == "slider":
                lo, hi = LIMITS[key]
                rows.append(Slider(key, label, getattr(s, key), lo, hi, fmt, w=w,
                                   on_change=lambda v, k=key: store.update(**{k: v})))
            else:
                rows.append(Toggle(key, label, getattr(s, key), w=w,
                                   on_change=lambda v, k=key: store.update(**{k: v})))
        content = Column(rows, gap=4, align="start", h=SETTINGS_HEIGHT)
        return Panel([Column(nav, gap=6, align="start", h=SETTINGS_HEIGHT), content], horizontal=True, pad=28,
                     gap=24, align="start")


def _environment_cards(s, store, w):
    names = [""] + environment.names()
    columns = max(4, -(-len(names) // 2))      # two rows, so the sliders below stay in view
    card_w = (w - 48 - (columns - 1) * theme.GAP * 2) // columns
    card_h = card_w // 2 + int(theme.SMALL * 1.6)
    cards = []
    for name in names:
        on = [("On", theme.ACCENT)] if name == s.environment else []
        cards.append(Card("env:" + name, name or "Dark room", environment.preview(name) if name else None,
                          card_w, card_h, badges=on, icon=Icon.SPHERE,
                          on_click=lambda name=name: store.update(environment=name)))
    return Row([Spacer(w=24), Grid(cards, columns)], w=w, align="start")


def _wrap(text, fnt, width):
    """Text split into lines that fit `width` pixels."""
    lines, line = [], ""
    for word in text.split():
        if line and fnt.getlength(line + " " + word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


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
        return Panel(rows, pad=20, gap=4, w=LIST_WIDTH + 40)


class DragChip(View):
    """A file being dragged: its name on a small card that follows the pointer."""

    def __init__(self, path):
        super().__init__(distance=1.2)
        self.path = path

    def state(self):
        return str(self.path)

    def build(self):
        icon = Icon.FOLDER if self.path.is_dir() else Icon.FILE
        return Row([Label(icon, size=theme.TEXT, color=theme.ACCENT_LIGHT, icon=True, w=56, h=theme.ROW),
                    Label(self.path.name, size=theme.SMALL - 4, h=theme.ROW)], pad=14, gap=6, bg=theme.BG,
                   radius=theme.RADIUS_CARD, border=theme.ACCENT)


RING_PX = 900                      # the action ring's square
RING_RADIUS = 285                  # where its buttons sit around the middle (px)


class _Ring(Widget):
    """Buttons placed around a circle, the title in the middle."""

    def __init__(self, title, buttons):
        super().__init__(RING_PX, RING_PX)
        self.title, self.buttons = title, buttons

    def children(self):
        return self.buttons

    def layout(self, x, y, w, h):
        super().layout(x, y, w, h)
        cx, cy = x + w / 2, y + h / 2
        n = max(1, len(self.buttons))
        for i, b in enumerate(self.buttons):
            a = -math.pi / 2 + 2 * math.pi * i / n          # the first one on top, then clockwise
            bw, bh = b.measure()
            b.layout(int(cx + math.cos(a) * RING_RADIUS - bw / 2), int(cy + math.sin(a) * RING_RADIUS - bh / 2),
                     bw, bh)

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        r = RING_RADIUS + 110
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(18, 20, 28, 215), outline=theme.EDGE, width=3)
        d.ellipse((cx - 120, cy - 120, cx + 120, cy + 120), fill=theme.CARD)
        fnt = theme.font(theme.SMALL - 6)
        d.text((cx, cy), theme.shorten(self.title, fnt, 220), font=fnt, fill=theme.FG, anchor="mm")
        super().draw(d, ctx)


class RingMenu(View):
    """The actions for what you pointed at, in a ring around the pointer: flick toward one. Same use as
    ContextMenu (items: [(label, icon, action, style)]); a click anywhere else closes it."""

    def __init__(self, title, items, close):
        super().__init__(distance=MENU_DISTANCE)
        self.title, self.items, self.close = title, items, close

    def _run(self, action):
        self.close()
        action()

    def build(self):
        buttons = [Button(f"ring:{i}", label, icon, h=theme.ROW + 10, size=theme.TEXT - 8,
                          style="danger" if style == "danger" else "normal", on_click=lambda a=action: self._run(a))
                   for i, (label, icon, action, style) in enumerate(self.items)]
        return _Ring(self.title, buttons)


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
        return Panel(rows, pad=14, gap=2)
