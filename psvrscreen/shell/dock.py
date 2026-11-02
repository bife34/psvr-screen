"""The dock (always below in front of you) and the music player just above it.

Dock:  ( Home ) | your windows as pills (click: show / select, right click: options, x: close) |
       Recenter  Settings  Mouse → PC  12:31
There is no Videos button: the Videos app opens from Home, so nobody sees what you watch.
"""
import time

from .. import apps
from ..core.settings import shown_title
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Label, Panel, Row, Spacer

DOCK_HEIGHT, DOCK_DISTANCE, DOCK_TILT = -0.42, 1.2, 28   # below eye level, leaning back to face you
TAB_WIDTH = int(theme.BAR * 4.0)
PLAYER_WIDTH = theme.BAR * 10


def _window_icon(panel):
    source = panel["source"]
    if source in ("main", "view1", "view2"):
        return Icon.MONITOR
    if source.startswith("app:"):
        return apps.NATIVE_APPS.get(source[4:], (None, Icon.APPS))[1]
    return Icon.WINDOW


class Dock(View):
    def __init__(self, shell):
        super().__init__(distance=DOCK_DISTANCE)
        self.shell = shell

    def state(self):
        s = self.shell.settings()
        selected = s.panels[s.selected]["id"] if s.panels else None
        return (tuple((p["id"], p["title"], p["source"], p.get("shown", True)) for p in s.panels), selected,
                s.private_mode, time.strftime("%H:%M"), getattr(self.shell.dialog, "kind", None))

    def build(self):
        shell = self.shell
        s = shell.settings()
        selected = s.panels[s.selected]["id"] if s.panels else None
        h = theme.BAR - 16

        def action(key, icon, label=None, style="flat"):
            return Button(key, label, icon, h=h, size=theme.TEXT - 4, style=style,
                          on_click=lambda: shell.dock_action(key))

        home_open = getattr(shell.dialog, "kind", None) == "home"
        home = Button("home", "Home", Icon.HOME, h=h, size=theme.TEXT - 4, style="normal", selected=home_open,
                      on_click=lambda: shell.dock_action("home"))
        tabs = []
        for p in s.panels:
            wid, shown = p["id"], p.get("shown", True)
            tabs.append(Row([
                Button("tab:" + wid, shown_title(p["title"], p.get("exe"), s.private_mode), _window_icon(p), h=h,
                       flex=1, size=theme.TEXT - 6, style="flat", muted=not shown,
                       on_click=lambda wid=wid: shell.show_window(wid),
                       on_right=lambda wid=wid: shell.window_menu(wid)),
                Button("close:" + wid, icon=Icon.CLOSE, w=int(h * 0.75), h=h - 16, size=theme.SMALL - 10,
                       style="danger", on_click=lambda wid=wid: shell.window_action(wid, "close")),
            ], w=TAB_WIDTH, h=h, bg=theme.SELECTED if wid == selected else theme.CARD, radius=h / 2, gap=4))
        if not tabs:
            tabs = [Label("No windows - open something from Home", size=theme.SMALL, color=theme.MUTED, h=h,
                          indent=12)]
        divider = Spacer(w=4)
        return Panel([home, divider, *tabs, divider,
                      action("recenter", Icon.RECENTER, "Recenter"),
                      action("global_settings", Icon.SETTINGS, "Settings"),
                      action("mouse_to_pc", Icon.MOUSE, "PC"),
                      Label(time.strftime("%H:%M"), size=theme.TEXT - 4, color=theme.MUTED, h=h, indent=8)],
                     horizontal=True, pad=10, gap=8, h=theme.BAR)


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
        h = theme.BAR - 20
        if playing:
            # Title and artist on one line: two lines would need fonts too small to read on the PSVR.
            text = playing["title"] + (f"  ·  {playing['artist']}" if playing["artist"] else "")
            if self.shell.settings().private_mode:
                text = "Playing" if playing["playing"] else "Paused"
            info = Label(text, size=theme.TEXT - 6, h=h, flex=1)
        else:
            info = Label("Nothing playing", size=theme.TEXT - 6, color=theme.MUTED, h=h, flex=1)
        media = self.shell.media

        def button(action, icon, style="flat"):
            return Button(action, icon=icon, w=h, h=h, style=style, size=theme.TEXT - 6,
                          on_click=lambda: media.command(action))

        return Panel([Label(Icon.MUSIC, size=theme.TEXT - 6, color=theme.ACCENT_LIGHT, w=h, h=h, align="center",
                            icon=True),
                      info,
                      button("previous", Icon.PREVIOUS),
                      button("play_pause", Icon.PAUSE if playing and playing["playing"] else Icon.PLAY, "primary"),
                      button("next", Icon.NEXT),
                      Button("hide", icon=Icon.CLOSE, w=int(h * 0.8), h=int(h * 0.8), size=theme.SMALL - 10,
                             style="danger", on_click=self.shell.dismiss_media)],
                     horizontal=True, w=PLAYER_WIDTH, h=theme.BAR - 4, pad=10, gap=6)
