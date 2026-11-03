"""Keyboard shortcuts as text ("Ctrl+Alt+P") <-> what Windows' RegisterHotKey needs."""
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Win": MOD_WIN}
KEY_ORDER = ("Ctrl", "Alt", "Shift", "Win")


def _virtual_key(name):
    name = name.upper()
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name)
    if name.startswith("F") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return {"SPACE": 0x20, "HOME": 0x24, "END": 0x23, "INSERT": 0x2D, "PAUSE": 0x13}.get(name)


def parse(text):
    """'Ctrl+Alt+P' -> (modifiers, virtual key), or None if it isn't a usable shortcut."""
    parts = [p.strip() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None   # a shortcut needs at least one modifier, or it would steal a normal key
    mods = 0
    for p in parts[:-1]:
        if p.capitalize() not in MODIFIERS:
            return None
        mods |= MODIFIERS[p.capitalize()]
    vk = _virtual_key(parts[-1])
    return (mods | MOD_NOREPEAT, vk) if vk else None


def from_tk(state, keysym):
    """Text for a key press captured in the control panel (Tk event state + keysym), or None."""
    if keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Win_L", "Win_R"):
        return None   # still holding modifiers only
    mods = [name for name, bit in (("Ctrl", 0x4), ("Alt", 0x20000), ("Shift", 0x1)) if state & bit]
    key = keysym.upper() if len(keysym) == 1 else keysym.capitalize()
    text = "+".join(mods + [key])
    return text if parse(text) else None


_KEYS = ([(chr(c), c) for c in range(ord("A"), ord("Z") + 1)] + [(chr(c), c) for c in range(ord("0"), ord("9") + 1)]
         + [(f"F{i}", 0x70 + i - 1) for i in range(1, 13)]
         + [("Space", 0x20), ("Home", 0x24), ("End", 0x23), ("Insert", 0x2D), ("Pause", 0x13)])
ESCAPE = 0x1B


def pressed_now():
    """The shortcut being pressed on the keyboard right now ("Ctrl+Alt+V"), "Escape", or None (nothing usable
    yet: no key, or no Ctrl / Alt / Shift with it). For changing a shortcut from inside VR."""
    import ctypes
    down = lambda vk: bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    if down(ESCAPE):
        return "Escape"
    mods = [name for name, vk in (("Ctrl", 0x11), ("Alt", 0x12), ("Shift", 0x10), ("Win", 0x5B)) if down(vk)]
    for name, vk in _KEYS:
        if down(vk):
            text = "+".join(mods + [name])
            return text if parse(text) else None
    return None
