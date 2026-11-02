"""In-VR interface: a top bar on each window (move, settings, minimize, close), a settings card per window,
and a fixed dock at the bottom (open windows as tabs, recenter, mouse back to the PC screen).

Each element is drawn with Pillow into an image; `hotspots` are the clickable areas in image pixels:
(x0, y0, x1, y1, action, payload). The renderer shows the image on a quad in 3D and the pointer turns a
hit into (action, payload).

Sizes are in image pixels and the renderer scales them by *viewing angle* (UI_PX_PER_DEGREE), not meters:
text is equally readable near or far. The PSVR shows ~11 pixels per degree, so a 44 px font (~1.8 degrees,
~20 headset pixels) is the smallest that reads comfortably.
"""
import math

from PIL import Image, ImageDraw, ImageFont

from .settings import LIMITS, locked

UI_PX_PER_DEGREE = 24                # image pixels per degree of view (~2x what the headset shows: smooth)
FONT = "seguisb.ttf"                 # Segoe UI Semibold
ICONS = "segmdl2.ttf"                # Segoe MDL2 Assets (Windows' own icons)
ICON_SETTINGS, ICON_MINIMIZE, ICON_CLOSE = "", "", ""
ICON_PIN, ICON_PINNED, ICON_UNLOCKED, ICON_LOCKED = "", "", "", ""
ICON_PREV, ICON_PLAY, ICON_PAUSE, ICON_NEXT, ICON_MUSIC = "", "", "", "", ""
ICON_GRIP, ICON_RECENTER, ICON_MOUSE, ICON_WINDOW, ICON_MONITOR = "", "", "", "", ""
ICON_ADD, ICON_ZOOM, ICON_APPS = "", "", ""
ICON_PAGE_UP, ICON_PAGE_DOWN = "", ""

BG = (24, 28, 36, 235)
BG_HOVER = (52, 62, 80, 255)
BG_SELECTED = (38, 64, 106, 255)
RED = (196, 43, 28, 255)
TEXT = (230, 233, 239, 255)
MUTED = (139, 147, 163, 255)
ACCENT = (79, 156, 249, 255)
ACCENT_LIGHT = (140, 192, 255, 255)

BAR_PX = 104          # window top bar height
BAR_IMAGE_WIDTH = 2300  # a window's bar is always this many px wide and scaled to the window's width
CARD_W_PX = 940       # settings card width
ROW_PX = 66           # one settings row (label, slider and value on one line)
DOCK_PX = 108         # dock / music player height
TEXT_PX = 44          # main label size (~1.8 degrees)
SMALL_PX = 38         # secondary text (artist, values)


def meters_per_px(distance):
    """Image pixel size in meters for an element at `distance`, so it covers 1/UI_PX_PER_DEGREE degree."""
    return distance * math.tan(math.radians(1.0)) / UI_PX_PER_DEGREE

# (panel field, label, format) - each one is a slider on the settings card
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

_fonts = {}


def _font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(name, size)
    return _fonts[key]


def _short(text, font, max_width):
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def _center_text(draw, box, text, font, fill):
    x0, y0, x1, y1 = box
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text(((x0 + x1 - (r - l)) / 2 - l, (y0 + y1 - (b - t)) / 2 - t), text, font=font, fill=fill)


def window_bar(panel, width_px, hover_action=None, settings_open=False, zoomed=False):
    """Top bar of one window, exactly as wide as the window (it is drawn on the same curve). Returns
    (image, hotspots)."""
    w, h = int(width_px), BAR_PX
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    pid = panel["id"]
    hotspots = []
    button_w = h + 10
    pos_locked, look_locked = panel.get("lock_position"), panel.get("lock_look")
    buttons = [("zoom", ICON_ZOOM, zoomed),
               ("lock_position", ICON_PINNED if pos_locked else ICON_PIN, pos_locked),
               ("lock_look", ICON_LOCKED if look_locked else ICON_UNLOCKED, look_locked),
               ("settings", ICON_SETTINGS, settings_open),
               ("minimize", ICON_MINIMIZE, False),
               ("close", ICON_CLOSE, False)]
    x = w - button_w * len(buttons)
    for action, icon, on in buttons:
        box = (x, 0, x + button_w, h)
        if hover_action == action:
            d.rectangle(box, fill=RED if action == "close" else BG_HOVER)
        elif on:
            d.rectangle(box, fill=BG_SELECTED)
        _center_text(d, box, icon, _font(ICONS, TEXT_PX), ACCENT_LIGHT if on else TEXT)
        hotspots.append((*box, action, pid))
        x += button_w
    title_box = (0, 0, w - button_w * len(buttons), h)
    if hover_action == "drag" and not pos_locked:
        d.rectangle(title_box, fill=BG_HOVER)
    _center_text(d, (0, 0, h, h), ICON_GRIP, _font(ICONS, SMALL_PX), MUTED)
    font = _font(FONT, TEXT_PX)
    title = _short(panel["title"] or "Window", font, title_box[2] - h - 10)
    d.text((h, h / 2), title, font=font, fill=TEXT, anchor="lm")
    hotspots.insert(0, (*title_box, "drag", pid))   # checked last (buttons first)
    return img, hotspots


def settings_card(panel, active_key=None):
    """Per-window settings: one slider per value (grab the knob and drag). `active_key` is the slider under
    the pointer / being dragged. Returns (image, hotspots); a slider's payload is (panel id, field, x0, x1)
    with x0..x1 its track in image pixels."""
    w, row = CARD_W_PX, ROW_PX
    head = int(row * 1.15)
    h = head + row * len(SETTINGS_ROWS) + 14
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    pid = panel["id"]
    d.text((24, head / 2 + 2), "Window settings", font=_font(FONT, TEXT_PX), fill=ACCENT, anchor="lm")
    hotspots = []
    label_font, value_font = _font(FONT, TEXT_PX), _font(FONT, SMALL_PX)
    # One line per setting: label | slider | value
    x0, x1 = int(w * 0.30), int(w * 0.76)
    for i, (key, label, fmt) in enumerate(SETTINGS_ROWS):
        top = head + row * i
        cy = top + row / 2
        value = panel.get(key, 0)
        frozen = locked(panel, key)
        d.text((24, cy), label, font=label_font, fill=MUTED if frozen else TEXT, anchor="lm")
        d.text((w - 24, cy), "locked" if frozen else fmt.format(value), font=value_font, fill=MUTED, anchor="rm")
        lo, hi = LIMITS[key]
        frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
        active = active_key == key and not frozen
        color = (90, 98, 112, 255) if frozen else ACCENT
        d.rounded_rectangle((x0, cy - 6, x1, cy + 6), 6, fill=(58, 66, 82, 255))
        knob_x = x0 + frac * (x1 - x0)
        d.rounded_rectangle((x0, cy - 6, knob_x, cy + 6), 6, fill=color)
        r = 20 if active else 15
        d.ellipse((knob_x - r, cy - r, knob_x + r, cy + r), fill=MUTED if frozen else TEXT, outline=color, width=4)
        if not frozen:
            hotspots.append((x0 - 24, top, x1 + 24, top + row, "slider", (pid, key, x0, x1)))
    hotspots.append((0, 0, w, h, "card", pid))   # anywhere else on the card: nothing (but not "outside")
    hotspots.insert(0, hotspots.pop())             # checked first, so sliders still win
    return img, hotspots


def dock(panels, selected_id, hover=None):
    """Fixed bottom bar: recenter, one tab per window, mouse back to the PC screen. Returns (image, hotspots)."""
    h = DOCK_PX
    tab_w, side_w = int(h * 4.2), int(h * 3.4)
    w = side_w * 5 + 24 + max(1, len(panels)) * (tab_w + 8) + 16
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    hotspots = []
    text_font, icon_font = _font(FONT, TEXT_PX - 4), _font(ICONS, TEXT_PX - 4)

    def side_button(x0, action, icon, label):
        box = (x0 + 6, 6, x0 + side_w - 6, h - 6)
        d.rounded_rectangle(box, 10, fill=BG_HOVER if hover == (action, None) else (40, 46, 58, 255))
        _center_text(d, (box[0], 6, box[0] + h * 0.8, h - 6), icon, icon_font, TEXT)
        d.text((box[0] + h * 0.8, h / 2), label, font=text_font, fill=TEXT, anchor="lm")
        hotspots.append((*box, action, None))

    side_button(0, "global_settings", ICON_SETTINGS, "Settings")
    side_button(side_w + 8, "recenter", ICON_RECENTER, "Recenter")
    side_button(side_w * 2 + 16, "apps_menu", ICON_APPS, "Apps")
    side_button(side_w * 3 + 24, "add_menu", ICON_ADD, "Add window")
    x = side_w * 4 + 32
    for p in panels:
        shown = p.get("shown", True)
        box = (x, 6, x + tab_w, h - 6)
        fill = BG_SELECTED if p["id"] == selected_id else (40, 46, 58, 255)
        if hover == ("tab", p["id"]):
            fill = BG_HOVER
        d.rounded_rectangle(box, 10, fill=fill)
        icon = ICON_WINDOW if p["source"] == "window" else ICON_MONITOR
        _center_text(d, (x, 6, x + h * 0.8, h - 6), icon, icon_font, TEXT if shown else MUTED)
        close_box = (x + tab_w - h * 0.8, 6, x + tab_w, h - 6)
        title = _short(p["title"] or "Window", text_font, tab_w - h * 1.6 - 10)
        d.text((x + h * 0.8, h / 2), title, font=text_font, fill=TEXT if shown else MUTED, anchor="lm")
        if not shown:   # minimized: underline dimmed
            d.line((x + 14, h - 12, x + tab_w - h * 0.8, h - 12), fill=MUTED, width=3)
        else:
            d.line((x + tab_w / 2 - 18, h - 10, x + tab_w / 2 + 18, h - 10), fill=ACCENT, width=4)
        # Close (x) on every tab, like a taskbar
        if hover == ("close", p["id"]):
            d.rounded_rectangle(close_box, 10, fill=RED)
        _center_text(d, close_box, ICON_CLOSE, _font(ICONS, SMALL_PX - 6), TEXT)
        hotspots.append((*box, "tab", p["id"]))
        hotspots.append((*close_box, "close", p["id"]))   # after the tab: wins on its own spot
        x += tab_w + 8
    side_button(w - side_w, "mouse_to_pc", ICON_MOUSE, "Mouse → PC")
    return img, hotspots


# Settings that apply to everything (the dock's Settings button): (kind, field, label, format)
GLOBAL_SETTINGS_ROWS = [
    ("title", None, "Mouse & controller", None),
    ("slider", "mouse_speed", "Mouse speed", "{:.1f}x"),
    ("toggle", "auto_add_windows", "Apps opened from VR open in VR", None),
    ("title", None, "Picture", None),
    ("toggle", "sharp_text", "Big round text in windows", None),
    ("slider", "lens_fov", "Field of view", "{:.0f}°"),
    ("slider", "ipd", "Eye distance", "{:.3f} m"),
    ("title", None, "Layout", None),
    ("toggle", "follow_head", "Windows follow my head", None),
]


def global_settings(settings, active_key=None, hover=None):
    """Settings card for everything (not one window). Sliders use the payload (None, field, x0, x1).
    Returns (image, hotspots)."""
    w, row = CARD_W_PX + 160, ROW_PX + 6
    h = int(row * 1.2) + row * len(GLOBAL_SETTINGS_ROWS) + 14
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.text((24, row * 0.6), "Settings", font=_font(FONT, TEXT_PX + 4), fill=TEXT, anchor="lm")
    hotspots = [(0, 0, w, h, "card", None)]
    label_font, value_font = _font(FONT, TEXT_PX), _font(FONT, SMALL_PX)
    x0, x1 = int(w * 0.42), int(w * 0.80)
    for i, (kind, key, label, fmt) in enumerate(GLOBAL_SETTINGS_ROWS):
        top = int(row * 1.2) + row * i
        cy = top + row / 2
        if kind == "title":
            d.text((24, cy + 6), label, font=_font(FONT, SMALL_PX), fill=ACCENT, anchor="lm")
            continue
        d.text((24, cy), label, font=label_font, fill=TEXT, anchor="lm")
        value = getattr(settings, key)
        if kind == "slider":
            lo, hi = LIMITS[key]
            frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
            d.rounded_rectangle((x0, cy - 6, x1, cy + 6), 6, fill=(58, 66, 82, 255))
            knob_x = x0 + frac * (x1 - x0)
            d.rounded_rectangle((x0, cy - 6, knob_x, cy + 6), 6, fill=ACCENT)
            r = 20 if active_key == key else 15
            d.ellipse((knob_x - r, cy - r, knob_x + r, cy + r), fill=TEXT, outline=ACCENT, width=4)
            d.text((w - 24, cy), fmt.format(value), font=value_font, fill=MUTED, anchor="rm")
            hotspots.append((x0 - 24, top, x1 + 24, top + row, "slider", (None, key, x0, x1)))
        else:   # toggle switch
            sx1, sx0 = w - 24, w - 24 - 96
            on = bool(value)
            fill = ACCENT if on else (58, 66, 82, 255)
            if hover == ("toggle", key):
                fill = ACCENT_LIGHT if on else BG_HOVER
            d.rounded_rectangle((sx0, cy - 24, sx1, cy + 24), 24, fill=fill)
            knob = sx1 - 24 if on else sx0 + 24
            d.ellipse((knob - 18, cy - 18, knob + 18, cy + 18), fill=TEXT)
            hotspots.append((24, top, w - 24, top + row, "toggle", key))
    return img, hotspots


LIST_PAGE_SIZE = 10


def _list_card(title, entries, action, empty_text, hover=None, page=0):
    """A list to pick from, in pages of LIST_PAGE_SIZE. entries: [(label, payload)]; a click on a row is
    (action, payload), the arrows are ("page", -1 / +1). Returns (image, hotspots)."""
    row = int(TEXT_PX * 1.7)
    w = 1100
    pages = max(1, (len(entries) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    shown = entries[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]
    h = row * (LIST_PAGE_SIZE + 1 if pages > 1 else len(shown) + 1) + 12
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.text((24, row / 2), title, font=_font(FONT, TEXT_PX), fill=ACCENT, anchor="lm")
    hotspots = [(0, 0, w, h, "card", None)]   # clicks between rows do nothing (don't close the list)
    if pages > 1:
        icon_font = _font(ICONS, TEXT_PX - 6)
        d.text((w - 2 * row - 24, row / 2), f"{page + 1} / {pages}", font=_font(FONT, SMALL_PX), fill=MUTED,
               anchor="rm")
        for i, (step, icon) in enumerate(((-1, ICON_PAGE_UP), (1, ICON_PAGE_DOWN))):
            box = (w - (2 - i) * row - 12, 6, w - (1 - i) * row - 18, row - 6)
            if hover == ("page", step):
                d.rounded_rectangle(box, 10, fill=BG_HOVER)
            _center_text(d, box, icon, icon_font, TEXT)
            hotspots.append((*box, "page", step))
    font = _font(FONT, TEXT_PX - 2)
    if not shown:
        d.text((24, row * 1.5), empty_text, font=font, fill=MUTED, anchor="lm")
    for i, (label, payload) in enumerate(shown):
        box = (12, row * (i + 1), w - 12, row * (i + 2) - 6)
        if hover == (action, payload):
            d.rounded_rectangle(box, 10, fill=BG_HOVER)
        d.text((36, (box[1] + box[3]) / 2), _short(label, font, w - 72), font=font, fill=TEXT, anchor="lm")
        hotspots.append((*box, action, payload))
    return img, hotspots


def window_picker(entries, hover=None, page=0):
    """Open windows to bring into VR (the dock's Add window button)."""
    return _list_card("Add a window to VR", entries, "pick", "No other open windows", hover, page)


def app_list(entries, hover=None, page=0):
    """Start menu apps to open directly in VR (the dock's Apps button)."""
    return _list_card("Open an app in VR", entries, "launch", "Loading apps...", hover, page)


def music_player(now_playing, hover=None):
    """Fixed player above the dock: what's playing + previous / play-pause / next. Returns (image, hotspots)."""
    h = DOCK_PX
    w = int(h * 9)
    img = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(img)
    icon_font = _font(ICONS, TEXT_PX - 4)
    _center_text(d, (0, 0, h, h), ICON_MUSIC, icon_font, ACCENT)
    buttons_w = int(h * 0.95) * 3
    text_w = w - h - buttons_w - 10
    if now_playing:
        # Title and artist on one line: two lines would need fonts too small to read on the PSVR.
        line = now_playing["title"] + (f"  ·  {now_playing['artist']}" if now_playing["artist"] else "")
        d.text((h, h / 2), _short(line, _font(FONT, TEXT_PX - 4), text_w), font=_font(FONT, TEXT_PX - 4),
               fill=TEXT, anchor="lm")
    else:
        d.text((h, h / 2), "Nothing playing", font=_font(FONT, TEXT_PX - 4), fill=MUTED, anchor="lm")
    hotspots = []
    x = w - buttons_w
    playing = bool(now_playing and now_playing["playing"])
    for action, icon in (("previous", ICON_PREV), ("play_pause", ICON_PAUSE if playing else ICON_PLAY),
                         ("next", ICON_NEXT)):
        box = (x + 4, 6, x + int(h * 0.95) - 4, h - 6)
        if hover == ("media", action):
            d.rounded_rectangle(box, 10, fill=BG_HOVER)
        _center_text(d, box, icon, icon_font, TEXT)
        hotspots.append((*box, "media", action))
        x += int(h * 0.95)
    return img, hotspots


def hotspot_at(hotspots, x, y):
    """Last matching hotspot wins (buttons are added after the big drag area)."""
    found = None
    for x0, y0, x1, y1, action, payload in hotspots:
        if x0 <= x <= x1 and y0 <= y <= y1:
            found = (action, payload)
    return found


def edge_frame(width, curvature):
    """Right edge of a (curved) panel in panel space: (x, z, angle) - for placing the settings card."""
    if curvature <= 0.001:
        return width / 2, 0.0, 0.0
    r = width / (2 * math.pi * curvature)
    theta = 0.5 * width / r
    return r * math.sin(theta), r * (1 - math.cos(theta)), theta
