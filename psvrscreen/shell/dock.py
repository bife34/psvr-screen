"""The dock (always below in front of you) and the music player just above it."""
from ..core.settings import shown_title
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Label, Row

DOCK_HEIGHT, DOCK_DISTANCE, DOCK_TILT = -0.42, 1.2, 28   # below eye level, leaning back to face you
TAB_WIDTH = int(theme.BAR * 4.2)
SIDE_WIDTH = int(theme.BAR * 3.4)


class Dock(View):
    """Settings, Recenter, Apps, Add window | one tab per window (click: show / select, x: close) | Mouse -> PC
    (no Videos button: the Videos app opens from Apps, so nobody sees what you watch)"""

    def __init__(self, shell):
        super().__init__(distance=DOCK_DISTANCE)
        self.shell = shell

    def state(self):
        s = self.shell.settings()
        selected = s.panels[s.selected]["id"] if s.panels else None
        return (tuple((p["id"], p["title"], p["source"], p.get("shown", True)) for p in s.panels), selected,
                s.private_mode)

    def build(self):
        shell = self.shell
        s = shell.settings()
        selected = s.panels[s.selected]["id"] if s.panels else None
        h = theme.BAR - 12

        def side(action, icon, label):
            return Button(action, label, icon, w=SIDE_WIDTH, h=h, size=theme.TEXT - 4,
                          on_click=lambda: shell.dock_action(action))

        tabs = []
        for p in s.panels:
            wid, shown = p["id"], p.get("shown", True)
            icon = Icon.MONITOR if p["source"] in ("main", "view1", "view2") else Icon.WINDOW
            tabs.append(Row([
                Button("tab:" + wid, shown_title(p["title"], p.get("exe"), s.private_mode), icon, h=h, flex=1, size=theme.TEXT - 4, style="flat",
                       muted=not shown, on_click=lambda wid=wid: shell.show_window(wid),
                       on_right=lambda wid=wid: shell.window_menu(wid)),
                Button("close:" + wid, icon=Icon.CLOSE, w=int(h * 0.8), h=h, size=theme.SMALL - 6, style="danger",
                       on_click=lambda wid=wid: shell.window_action(wid, "close")),
            ], w=TAB_WIDTH, h=h, bg=theme.SELECTED if wid == selected else theme.BUTTON, radius=theme.RADIUS))
        return Row([side("global_settings", Icon.SETTINGS, "Settings"),
                    side("recenter", Icon.RECENTER, "Recenter"),
                    side("apps", Icon.APPS, "Apps"),
                    side("add_window", Icon.ADD, "Add window"),
                    *tabs,
                    side("mouse_to_pc", Icon.MOUSE, "Mouse → PC")],
                   gap=8, pad=6, h=theme.BAR, bg=theme.BG)


class Player(View):
    """What's playing (title · artist), previous / play-pause / next, and x to hide the row."""

    def __init__(self, shell):
        super().__init__(distance=DOCK_DISTANCE)
        self.shell = shell

    def state(self):
        playing = self.shell.media.now_playing
        return (tuple(sorted(playing.items())) if playing else None), self.shell.settings().private_mode

    def build(self):
        playing = self.shell.media.now_playing
        h = theme.BAR
        if playing:
            # Title and artist on one line: two lines would need fonts too small to read on the PSVR.
            text = playing["title"] + (f"  ·  {playing['artist']}" if playing["artist"] else "")
            if self.shell.settings().private_mode:
                text = "Playing" if playing["playing"] else "Paused"
            info = Label(text, size=theme.TEXT - 4, h=h, flex=1)
        else:
            info = Label("Nothing playing", size=theme.TEXT - 4, color=theme.MUTED, h=h, flex=1)
        media = self.shell.media

        def button(action, icon):
            return Button(action, icon=icon, w=int(h * 0.95) - 8, h=h - 12, style="flat",
                          on_click=lambda: media.command(action))

        return Row([Label(Icon.MUSIC, size=theme.TEXT - 4, color=theme.ACCENT, w=h, h=h, align="center", icon=True),
                    info,
                    button("previous", Icon.PREVIOUS),
                    button("play_pause", Icon.PAUSE if playing and playing["playing"] else Icon.PLAY),
                    button("next", Icon.NEXT),
                    Button("hide", icon=Icon.CLOSE, w=int(h * 0.75), h=h - 12, size=theme.SMALL - 6, style="danger",
                           on_click=self.shell.dismiss_media)],
                   w=h * 9 + int(h * 0.75) + 4, h=h, gap=4, bg=theme.BG)
