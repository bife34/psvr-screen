"""What the shell adds to every window: the title bar (shown while you point at the window) and the
settings card. Both are views; `shell` is the Shell they act on."""
from ..core.settings import LIMITS, locked, shown_title
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Column, Handle, Label, Row, Slider

BAR_IMAGE_WIDTH = 2300   # the bar is drawn this wide and scaled to its window's width
CARD_WIDTH = 940

# (window field, label, format) - one slider each on the settings card
SETTINGS_ROWS = [
    ("width", "Size", "{:.2f} m"),
    ("distance", "Distance", "{:.2f} m"),
    ("height", "Height", "{:+.2f} m"),
    ("curvature", "Curve", "{:.2f}"),
    ("tilt", "Tilt", "{:+.0f}°"),
    ("turn", "Turn", "{:+.0f}°"),
    ("roll", "Rotate", "{:+.0f}°"),
    ("text_zoom", "Text size", "{:.1f}x"),
    ("opacity", "Opacity", "{:.0%}"),
]


class TitleBar(View):
    """Grab the title to move the window (wheel = closer / farther, wheel button = bend); buttons: zoom
    lens, position lock, look lock, settings, minimize, close. Same width and curve as its window."""

    def __init__(self, shell, window_id):
        super().__init__(m_per_px=1.0)
        self.shell, self.window_id = shell, window_id

    def state(self):
        p = self.shell.panel(self.window_id) or {}
        return (p.get("title"), self.shell.settings().private_mode, p.get("lock_position"), p.get("lock_look"),
                self.window_id in self.shell.zoomed, self.window_id in self.shell.settings_open)

    def build(self):
        p = self.shell.panel(self.window_id) or {}
        wid, s = self.window_id, self.shell
        h = theme.BAR
        pos_locked, look_locked = p.get("lock_position", False), p.get("lock_look", False)

        def button(action, icon, on=False, style="flat"):
            return Button(action, icon=icon, w=h + 10, h=h, style=style, selected=on,
                          on_click=lambda: s.window_action(wid, action))

        title = Row([Label(Icon.GRIP, size=theme.SMALL, color=theme.MUTED, w=h, h=h, align="center", icon=True),
                     Label(shown_title(p.get("title"), p.get("exe"), self.shell.settings().private_mode), h=h, flex=1)],
                    h=h, flex=1, align="stretch")
        grip = Handle("move", title, flex=1, h=h, hover_bg=None if pos_locked else theme.HOVER,
                      on_press=lambda e: s.grab_window(wid, e), on_drag=s.gesture_drag,
                      on_release=s.gesture_release, on_wheel=s.gesture_wheel)
        return Row([grip,
                    button("zoom", Icon.ZOOM, wid in s.zoomed),
                    button("lock_position", Icon.PINNED if pos_locked else Icon.PIN, pos_locked),
                    button("lock_look", Icon.LOCKED if look_locked else Icon.UNLOCKED, look_locked),
                    button("settings", Icon.SETTINGS, wid in s.settings_open),
                    button("minimize", Icon.MINIMIZE),
                    button("close", Icon.CLOSE, style="danger")],
                   w=BAR_IMAGE_WIDTH, h=h, bg=theme.BG, align="stretch")


class SettingsCard(View):
    """One slider per setting of a window (locked ones are shown greyed out)."""

    def __init__(self, shell, window_id, distance):
        super().__init__(distance=distance)
        self.shell, self.window_id = shell, window_id

    def state(self):
        p = self.shell.panel(self.window_id) or {}
        return tuple(round(p.get(k, 0), 3) for k, *_ in SETTINGS_ROWS) + (p.get("lock_position"),
                                                                              p.get("lock_look"))

    def build(self):
        p = self.shell.panel(self.window_id) or {}
        wid, s = self.window_id, self.shell
        rows = [Label("Window settings", color=theme.ACCENT, w=CARD_WIDTH, h=int(theme.ROW * 1.15), indent=24)]
        for key, label, fmt in SETTINGS_ROWS:
            lo, hi = LIMITS[key]
            rows.append(Slider(key, label, p.get(key, 0), lo, hi, fmt, w=CARD_WIDTH, disabled=locked(p, key),
                               on_change=lambda v, k=key: s.set_window_value(wid, k, v)))
        return Column(rows, pad=12, bg=theme.BG, radius=theme.RADIUS, w=CARD_WIDTH + 24)
