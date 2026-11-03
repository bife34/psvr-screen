"""Home: the launcher (the dock's Home button). Big tiles for the VR apps and the PC's things, the time.

Opens in front of you like a dialog; picking something closes it.
"""
import time

from ..win import integrated
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Card, Grid, Label, Panel, Row, Spacer, Tile
from ..video.thumbs import THUMBNAILS
from .dialogs import DIALOG_DISTANCE

TILE = 300
COLUMNS = 3
CONTINUE_SHOWN = 3
# key, name, icon, color, subtitle
TILES = [
    ("videos", "Videos", Icon.VIDEO, (10, 132, 255, 255), "180° · 360° · 3D"),
    ("photos", "Photos", Icon.PHOTO, (255, 149, 0, 255), "Pictures · 360°"),
    ("games", "Games", Icon.GAMEPAD, (255, 59, 48, 255), "Play on a big screen"),
    ("files", "Files", Icon.FOLDER, (255, 196, 0, 255), "Folders · drives"),
    ("apps", "Apps", Icon.APPS, (175, 82, 222, 255), "PC programs"),
    ("add_window", "Add window", Icon.ADD, (52, 199, 89, 255), "An open window"),
    ("settings", "Settings", Icon.SETTINGS, (99, 99, 110, 255), "Eyes · privacy"),
]


WINDOWS_TILES = ("apps", "add_window")   # Windows programs: only with settings.windows_apps


class Home(View):
    def __init__(self, shell):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell = shell

    def state(self):
        watching = tuple(w["path"] + str(w["position"]) for _, w in self.shell.continue_watching())
        return time.strftime("%H:%M"), self.shell.settings().private_mode, watching, THUMBNAILS.version

    def build(self):
        shell = self.shell
        width = COLUMNS * TILE + (COLUMNS - 1) * theme.GAP * 2
        header = Row([Label("Home", size=theme.HUGE, h=int(theme.HUGE * 1.5), indent=8),
                      Spacer(flex=1),
                      Label(time.strftime("%H:%M"), size=theme.TITLE, color=theme.FG, h=int(theme.HUGE * 1.5)),
                      Label(time.strftime("  %a %d %b"), size=theme.SMALL, color=theme.MUTED, h=int(theme.HUGE * 1.5))],
                     w=width)
        windows_apps = shell.settings().windows_apps
        tiles = [Tile("tile:" + key, name, icon, color, on_click=lambda key=key: shell.home_action(key),
                      w=TILE, h=TILE, subtitle=subtitle)
                 for key, name, icon, color, subtitle in TILES
                 if windows_apps or key not in WINDOWS_TILES]
        # Windows programs that work in VR (win/integrated.py), when they're on this PC: before Settings
        for key, app in integrated.APPS.items():
            if integrated.installed(key):
                tiles.insert(len(tiles) - 1, Tile("tile:" + key, app["name"], app["icon"], app["color"],
                                                  on_click=lambda key=key: shell.home_action(key), w=TILE, h=TILE,
                                                  subtitle=app["subtitle"]))
        rows = [header]
        watching = shell.continue_watching()[:CONTINUE_SHOWN]
        if watching:
            card_h = int(TILE * 9 / 16) + int(theme.SMALL * 2.6)
            cards = []
            for i, (path, entry) in enumerate(watching):
                image, _ = THUMBNAILS.get(path)
                left = max(0, int(entry["duration"] - entry["position"]))
                cards.append(Card(f"continue:{i}", path.stem, image, TILE, card_h, icon=Icon.VIDEO,
                                  subtitle=f"{left // 60} min left" if left >= 60 else f"{left} s left",
                                  progress=entry["position"] / entry["duration"],
                                  on_click=lambda e=entry: shell.resume(e),
                                  on_right=lambda e=entry: shell.watching_menu(e)))
            rows += [Label("Continue watching", size=theme.TEXT - 4, color=theme.MUTED, w=width, h=theme.ROW,
                           indent=8),
                     Grid(cards, COLUMNS),
                     Label("Apps", size=theme.TEXT - 4, color=theme.MUTED, w=width, h=theme.ROW, indent=8)]
        rows.append(Grid(tiles, COLUMNS))
        if shell.settings().private_mode:
            rows.append(Label("Private mode is on - titles are hidden", size=theme.SMALL - 6, color=theme.MUTED,
                              w=width, h=theme.SMALL, align="center"))
        return Panel(rows, pad=36, gap=theme.GAP * 2)
