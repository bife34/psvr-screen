"""The look of the VR interface: colors, fonts, sizes and icons - change them here, for everything.

Design: dark glass. Panels are see-through dark glass with a fine light edge and big round corners; cards
and buttons inside are a little lighter; one accent blue marks what is selected or the main action. Fonts and
icons are Windows 11's own (Segoe UI Variable, Segoe Fluent Icons), so nothing has to be installed.

Sizes are in image pixels. Views are scaled by *viewing angle* (PX_PER_DEGREE), not meters, so text is
equally readable near or far. The PSVR shows ~11 pixels per degree; a 44 px font (~1.8 degrees, ~20 headset
pixels) is the smallest that reads comfortably. Images are drawn at ~2x the headset's detail: smooth text.
"""
from PIL import ImageFont

PX_PER_DEGREE = 24

FONT = "SegUIVar.ttf"                # Segoe UI Variable (Windows 11), weights below
FONT_FALLBACK = "seguisb.ttf"        # Segoe UI Semibold (Windows 10)
ICON_FONT = "SegoeIcons.ttf"         # Segoe Fluent Icons (Windows 11)
ICON_FALLBACK = "segmdl2.ttf"        # Segoe MDL2 Assets (Windows 10, same code points)
WEIGHTS = {"regular": "Regular", "semibold": "Semibold Text", "bold": "Bold Display"}

# Text sizes
TEXT = 44                            # labels, buttons (~1.8 degrees)
SMALL = 38                           # values, secondary text
TITLE = 52                           # panel titles
HUGE = 72                            # big headings (Home)

# Colors (RGBA)
BG = (18, 20, 26, 225)               # glass panels
EDGE = (255, 255, 255, 34)           # the fine light edge around panels
CARD = (36, 40, 50, 255)             # cards, tiles, list rows
BUTTON = (44, 49, 61, 255)
HOVER = (62, 69, 86, 255)
SELECTED = (24, 66, 128, 255)
DANGER = (235, 64, 52, 255)
TRACK = (66, 72, 88, 255)
DISABLED = (96, 102, 116, 255)
FG = (244, 245, 248, 255)
MUTED = (150, 156, 172, 255)
ACCENT = (10, 132, 255, 255)
ACCENT_LIGHT = (110, 180, 255, 255)

# Shapes
RADIUS = 18                          # buttons, rows
RADIUS_CARD = 26                     # cards and tiles
RADIUS_PANEL = 40                    # panels (dock, dialogs, apps)
ROW = 66                             # one row in cards and lists
BAR = 104                            # height of window bars and the dock
GAP = 12                             # the space between things


# Icons (Segoe Fluent Icons / MDL2 Assets code points)
class Icon:
    SETTINGS, MINIMIZE, CLOSE = "", "", ""
    PIN, PINNED, UNLOCKED, LOCKED = "", "", "", ""
    PREVIOUS, PLAY, PAUSE, NEXT, MUSIC = "", "", "", "", ""
    GRIP, RECENTER, MOUSE, WINDOW, MONITOR = "", "", "", "", ""
    ADD, ZOOM, APPS = "", "", ""
    PAGE_UP, PAGE_DOWN = "", ""
    FOLDER, FILE, BACK, UP = "", "", "", ""
    VIDEO = ""
    HOME, PHOTO, GLOBE, EYE, PRIVATE = "", "", "", "", ""
    INFO, CLOCK, GAMEPAD, KEYBOARD, CHEVRON = "", "", "", "", ""
    VOLUME, MUTE, GALLERY, SHUFFLE = "", "", "", ""
    MIC, POWER = "", ""
    SPHERE, DRIVE, STAR, HISTORY = "", "", "", ""


_fonts = {}


def font(size, icons=False, weight="semibold"):
    key = (icons, size, weight)
    if key not in _fonts:
        try:
            f = ImageFont.truetype(ICON_FONT if icons else FONT, size)
            if not icons:
                f.set_variation_by_name(WEIGHTS[weight])
        except (OSError, ValueError):
            f = ImageFont.truetype(ICON_FALLBACK if icons else FONT_FALLBACK, size)
        _fonts[key] = f
    return _fonts[key]


def shorten(text, fnt, max_width):
    """Cut text with "…" so it fits `max_width` pixels."""
    if fnt.getlength(text) <= max_width:
        return text
    while text and fnt.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"
