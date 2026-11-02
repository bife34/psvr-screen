"""Games: the PC's installed games (win/games.py) as a grid of covers. Click one: it starts, and its window comes
into VR as a big screen in front of you (shell.start_game); while you play, the DS4 and the mouse belong to the
game - Share + Options brings them back to VR.
"""
import threading

from PIL import Image

from ..ui import theme
from ..ui.theme import Icon
from ..ui.widgets import Button, Card, Grid, Label, Panel, Row, Spacer
from ..win import games
from .base import Page, ViewContent

PAGE_WIDTH = 1640
COLUMNS = 3
ROWS = 3
CARD_W = (PAGE_WIDTH - 2 * 32 - (COLUMNS - 1) * theme.GAP * 2) // COLUMNS
CARD_H = int(CARD_W * 215 / 460) + int(theme.SMALL * 1.6)   # Steam's header pictures are 460 x 215
COVER_WIDTH = 600


class GamesPage(Page):
    def __init__(self, host):
        super().__init__(host)
        self.games, self.covers, self.loading, self.page = [], {}, True, 0
        self.version = 0
        threading.Thread(target=self._load, daemon=True, name="games").start()

    def _load(self):
        self.games, self.loading = games.installed_games(), False
        self.version += 1
        for game in self.games:
            if game.cover:
                try:
                    with Image.open(game.cover) as image:
                        image = image.convert("RGB")
                        image.thumbnail((COVER_WIDTH, COVER_WIDTH))
                    self.covers[game.id] = image
                    self.version += 1
                except OSError:
                    pass

    def state(self):
        return self.version, self.page, self.loading

    def _turn(self, step, pages):
        self.page = max(0, min(pages - 1, self.page + step))

    def build(self):
        w, h = PAGE_WIDTH - 64, theme.ROW
        rows = [Row([Label(Icon.GAMEPAD, size=theme.TITLE, color=theme.ACCENT_LIGHT, icon=True, h=int(h * 1.4), w=70),
                     Label("Games", size=theme.HUGE - 12, h=int(h * 1.4)), Spacer(flex=1)], w=w, h=int(h * 1.4)),
                Label("Click a game to play it on a big screen.  Share + Options: back to VR.", size=theme.SMALL - 6,
                      color=theme.MUTED, w=w, h=h, indent=8)]
        per_page = COLUMNS * ROWS
        grid_h = ROWS * CARD_H + (ROWS - 1) * theme.GAP * 2
        pages = max(1, (len(self.games) + per_page - 1) // per_page)
        self.page = min(self.page, pages - 1)
        shown = self.games[self.page * per_page:(self.page + 1) * per_page]
        cards = [Card(f"game:{self.page}:{i}", g.name, self.covers.get(g.id), CARD_W, CARD_H, icon=Icon.GAMEPAD,
                      on_click=lambda g=g: self.host.start_game(g))
                 for i, g in enumerate(shown)]
        if self.loading:
            body = Label("Looking for games...", size=theme.SMALL, color=theme.MUTED, w=w, h=grid_h, align="center")
        elif not cards:
            body = Label("No games found (Steam)", size=theme.SMALL, color=theme.MUTED, w=w, h=grid_h, align="center")
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


class GamesApp(ViewContent):
    icon = Icon.GAMEPAD

    def __init__(self, panel, host):
        super().__init__(panel, host, GamesPage(host))
