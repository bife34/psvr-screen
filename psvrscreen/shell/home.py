"""Home: the launcher (the dock's Home button). Big tiles for the VR apps and the PC's things, the time.

Opens in front of you like a dialog; picking something closes it.
"""
import time

from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Grid, Label, Panel, Row, Spacer, Tile
from .dialogs import DIALOG_DISTANCE

TILE = 300
COLUMNS = 3
# key, name, icon, color, subtitle
TILES = [
    ("videos", "Videos", Icon.VIDEO, (10, 132, 255, 255), "180° · 360° · 3D"),
    ("photos", "Photos", Icon.PHOTO, (255, 149, 0, 255), "Pictures · 360°"),
    ("files", "Files", Icon.FOLDER, (255, 196, 0, 255), "File Explorer"),
    ("apps", "Apps", Icon.APPS, (175, 82, 222, 255), "PC programs"),
    ("add_window", "Add window", Icon.ADD, (52, 199, 89, 255), "An open window"),
    ("settings", "Settings", Icon.SETTINGS, (99, 99, 110, 255), "Eyes · privacy"),
]


class Home(View):
    def __init__(self, shell):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell = shell

    def state(self):
        return time.strftime("%H:%M"), self.shell.settings().private_mode

    def build(self):
        shell = self.shell
        width = COLUMNS * TILE + (COLUMNS - 1) * theme.GAP * 2
        header = Row([Label("Home", size=theme.HUGE, h=int(theme.HUGE * 1.5), indent=8),
                      Spacer(flex=1),
                      Label(time.strftime("%H:%M"), size=theme.TITLE, color=theme.FG, h=int(theme.HUGE * 1.5)),
                      Label(time.strftime("  %a %d %b"), size=theme.SMALL, color=theme.MUTED, h=int(theme.HUGE * 1.5))],
                     w=width)
        tiles = [Tile("tile:" + key, name, icon, color, on_click=lambda key=key: shell.home_action(key),
                      w=TILE, h=TILE, subtitle=subtitle)
                 for key, name, icon, color, subtitle in TILES]
        rows = [header, Grid(tiles, COLUMNS)]
        if shell.settings().private_mode:
            rows.append(Label("Private mode is on - titles are hidden", size=theme.SMALL - 6, color=theme.MUTED,
                              w=width, h=theme.SMALL, align="center"))
        return Panel(rows, pad=36, gap=theme.GAP * 2)
