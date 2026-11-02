"""The look of the VR interface: colors, fonts, sizes and icons - change them here, for everything.

Sizes are in image pixels. Views are scaled by *viewing angle* (PX_PER_DEGREE), not meters, so text is
equally readable near or far. The PSVR shows ~11 pixels per degree; a 44 px font (~1.8 degrees, ~20 headset
pixels) is the smallest that reads comfortably. Images are drawn at ~2x the headset's detail: smooth text.
"""
from PIL import ImageFont

PX_PER_DEGREE = 24

FONT = "seguisb.ttf"                 # Segoe UI Semibold
ICON_FONT = "segmdl2.ttf"            # Segoe MDL2 Assets (Windows' own icons)

# Text sizes
TEXT = 44                            # labels, buttons (~1.8 degrees)
SMALL = 38                           # values, secondary text
TITLE = 48

# Colors (RGBA)
BG = (24, 28, 36, 235)
BUTTON = (40, 46, 58, 255)
HOVER = (52, 62, 80, 255)
SELECTED = (38, 64, 106, 255)
DANGER = (196, 43, 28, 255)
TRACK = (58, 66, 82, 255)
DISABLED = (90, 98, 112, 255)
FG = (230, 233, 239, 255)
MUTED = (139, 147, 163, 255)
ACCENT = (79, 156, 249, 255)
ACCENT_LIGHT = (140, 192, 255, 255)

RADIUS = 10
ROW = 66                             # one row in cards and lists
BAR = 104                            # height of window bars and the dock

# Icons (Segoe MDL2 Assets code points)
class Icon:
    SETTINGS, MINIMIZE, CLOSE = "", "", ""
    PIN, PINNED, UNLOCKED, LOCKED = "", "", "", ""
    PREVIOUS, PLAY, PAUSE, NEXT, MUSIC = "", "", "", "", ""
    GRIP, RECENTER, MOUSE, WINDOW, MONITOR = "", "", "", "", ""
    ADD, ZOOM, APPS = "", "", ""
    PAGE_UP, PAGE_DOWN = "", ""
    FOLDER, FILE, BACK, UP = "", "", "", ""
    VIDEO = ""


_fonts = {}


def font(size, icons=False):
    key = (icons, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(ICON_FONT if icons else FONT, size)
    return _fonts[key]


def shorten(text, fnt, max_width):
    """Cut text with "…" so it fits `max_width` pixels."""
    if fnt.getlength(text) <= max_width:
        return text
    while text and fnt.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"
