"""Windows display helpers: find the main / headset / VR monitor, change the VR monitor's text size,
keep the headset at 90 Hz, and keep the mouse off the headset's screen.

The VR monitor is the Virtual Display Driver (monitor id MTT1337), see tools/install_vr_monitor.ps1.
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

VR_MONITOR_ID = "MTT1337"
VR_MONITOR_MODE = (2560, 1440, 225)   # the VR monitor: many pixels and Windows' biggest scaling there
HEADSET_MONITOR_ID = "SNYB403"
HEADSET_HZ = 90

# Text size levels for the VR monitor: (width, height, windows scale %). Bigger level = bigger text.
# Windows only allows some scales per resolution (1080p: up to 175%, 900p: 150%, 720p: none).
TEXT_SIZES = [
    (1920, 1080, 100),
    (1920, 1080, 125),
    (1920, 1080, 150),
    (1920, 1080, 175),
    (1600, 900, 150),
]

ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x01
DM_POSITION, DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x20, 0x80000, 0x100000, 0x400000
DPI_STEPS = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]


class _DEVMODE(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


VIEW_MODE = (1920, 1080, 175)   # extra virtual monitors ("Desktop views"): a whole desktop with big text
# view2 is the 4K screen: a whole desktop as roomy as the main one but twice as sharp - for big screens and
# games (needs 3840x2160 in the driver's list, tools/set_vr_monitors.ps1; until then 2560x1440)
VIEW_MODES = {"view2": (3840, 2160, 200)}
FALLBACK_4K = (2560, 1440, 150)


def view_mode(kind):
    return VIEW_MODES.get(kind, VIEW_MODE)


def view_title(kind):
    """What an extra virtual monitor is called."""
    return "4K screen" if kind in VIEW_MODES else f"Desktop view {kind[4:]}"


def view_description(kind):
    return ("4K screen (sharp: big screens, games, videos)" if kind in VIEW_MODES
            else f"Desktop view {kind[4:]} (a whole desktop with big text)")


def view_panel_layout(kind):
    """Where a new window showing this monitor goes: the 4K screen big and farther away."""
    return (dict(width=6.0, distance=4.0, curvature=0.1) if kind in VIEW_MODES
            else dict(width=3.0, distance=1.6, curvature=0.2))


class Display:
    def __init__(self, name, monitor_id, left, top, width, height, hz, primary):
        self.name, self.monitor_id = name, monitor_id
        self.left, self.top, self.width, self.height, self.hz, self.primary = left, top, width, height, hz, primary
        self.kind = "other"

    def _initial_kind(self):
        if HEADSET_MONITOR_ID in self.monitor_id:
            return "headset"
        return "main" if self.primary else "other"

    @property
    def rect(self):
        return self.left, self.top, self.left + self.width, self.top + self.height


def _devmode(name):
    dm = _DEVMODE()
    dm.dmSize = ctypes.sizeof(_DEVMODE)
    if not user32.EnumDisplaySettingsW(name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return dm


def list_displays():
    """Active displays attached to the desktop."""
    result = []
    i = 0
    while True:
        adapter = _DISPLAY_DEVICE(cb=ctypes.sizeof(_DISPLAY_DEVICE))
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(adapter), 0):
            break
        i += 1
        if not adapter.StateFlags & 0x1:     # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            continue
        # An output can list old monitors too (e.g. one that used the same port before): take the active one.
        monitor_id = ""
        j = 0
        while True:
            monitor = _DISPLAY_DEVICE(cb=ctypes.sizeof(_DISPLAY_DEVICE))
            if not user32.EnumDisplayDevicesW(adapter.DeviceName, j, ctypes.byref(monitor), 0):
                break
            j += 1
            if monitor.StateFlags & 0x1:     # DISPLAY_DEVICE_ACTIVE
                monitor_id = monitor.DeviceID
                break
        dm = _devmode(adapter.DeviceName)
        if dm is None or dm.dmPelsWidth == 0:
            continue
        result.append(Display(adapter.DeviceName, monitor_id, dm.dmPositionX, dm.dmPositionY,
                              dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency,
                              bool(adapter.StateFlags & 0x4)))   # DISPLAY_DEVICE_PRIMARY_DEVICE
    # Virtual monitors: the first one holds the VR windows ("vr"), the others are Desktop views
    virtual = sorted((d for d in result if VR_MONITOR_ID in d.monitor_id), key=lambda d: _display_number(d.name))
    for d in result:
        d.kind = d._initial_kind()
    for i, d in enumerate(virtual):
        d.kind = "vr" if i == 0 else f"view{i}"
    return result


def _display_number(name):
    digits = "".join(c for c in name if c.isdigit())
    return int(digits) if digits else 0


def find(kind):
    return next((d for d in list_displays() if d.kind == kind), None)


def _change(name, dm):
    return user32.ChangeDisplaySettingsExW(name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)


def ensure_headset_hz():
    """Windows sometimes drops the headset to 59 Hz after a display change: the headset then shows black."""
    hmd = find("headset")
    if hmd is None or hmd.hz == HEADSET_HZ:
        return False
    dm = _devmode(hmd.name)
    dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = 1920, 1080, HEADSET_HZ
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    _change(hmd.name, dm)
    return True


def place_vr_monitor_left_of_main():
    """Virtual monitors in a row left of the main one: [view2][view1][vr][main][headset]. The headset's
    screen (right of main) stays out of the way of the mouse."""
    displays = list_displays()
    main = next((d for d in displays if d.kind == "main"), None)
    chain = [d for kind in ("vr", "view1", "view2", "view3")
             for d in displays if d.kind == kind]
    if main is None or not chain:
        return
    x = main.left
    changed = False
    for d in chain:
        x -= d.width
        if (d.left, d.top) != (x, main.top):
            dm = _devmode(d.name)
            dm.dmPositionX, dm.dmPositionY = x, main.top
            dm.dmFields = DM_POSITION
            _change(d.name, dm)
            changed = True
    if changed:
        ensure_headset_hz()


def prepare_views():
    """Desktop views: their own resolution and big Windows scaling (only those monitors change)."""
    for d in list_displays():
        if d.kind.startswith("view"):
            mode = view_mode(d.kind)
            if (d.width, d.height) != mode[:2]:
                dm = _devmode(d.name)
                dm.dmPelsWidth, dm.dmPelsHeight = mode[:2]
                dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
                if _change(d.name, dm) != 0 and d.kind in VIEW_MODES:   # the driver doesn't offer 4K yet
                    mode = FALLBACK_4K
                    if (d.width, d.height) != mode[:2]:
                        dm.dmPelsWidth, dm.dmPelsHeight = mode[:2]
                        _change(d.name, dm)
            d = find(d.kind)
            if d is not None and get_scale(d) != mode[2]:
                set_scale(d, mode[2])
    place_vr_monitor_left_of_main()


VIRTUAL_ADAPTER = "Virtual Display Driver"
ENUM_REGISTRY_SETTINGS = -2
CDS_NORESET = 0x10000000


def _virtual_adapters():
    """[(adapter name, attached to the desktop)] of the virtual monitors (also the ones turned off)."""
    found, i = [], 0
    while True:
        adapter = _DISPLAY_DEVICE(cb=ctypes.sizeof(_DISPLAY_DEVICE))
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(adapter), 0):
            return sorted(found, key=lambda a: _display_number(a[0]))
        i += 1
        if adapter.DeviceString == VIRTUAL_ADAPTER:
            found.append((adapter.DeviceName, bool(adapter.StateFlags & 0x1)))


def turn_off_virtual_monitors():
    """The app quits: the invisible monitors go away, so Windows can't put (or later reopen) a window where
    nobody sees it - it moves their windows to the main monitor itself."""
    changed = False
    for name, attached in _virtual_adapters():
        if attached:
            dm = _devmode(name) or _DEVMODE(dmSize=ctypes.sizeof(_DEVMODE))
            dm.dmPelsWidth = dm.dmPelsHeight = dm.dmPositionX = dm.dmPositionY = 0
            dm.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT
            user32.ChangeDisplaySettingsExW(name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY | CDS_NORESET, None)
            changed = True
    if changed:
        user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
        ensure_headset_hz()
    return changed


def turn_on_virtual_monitors():
    """The app starts: the invisible monitors come back (left of the main one, their last size)."""
    main = find("main")
    x = main.left if main else 0
    changed = False
    for name, attached in _virtual_adapters():
        if attached:
            continue
        dm = _DEVMODE()
        dm.dmSize = ctypes.sizeof(_DEVMODE)
        if not user32.EnumDisplaySettingsW(name, ENUM_REGISTRY_SETTINGS, ctypes.byref(dm)) or dm.dmPelsWidth == 0:
            dm.dmPelsWidth, dm.dmPelsHeight = VIEW_MODE[:2]
        x -= dm.dmPelsWidth
        dm.dmPositionX, dm.dmPositionY = x, main.top if main else 0
        dm.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT
        user32.ChangeDisplaySettingsExW(name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY | CDS_NORESET, None)
        changed = True
    if changed:
        user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
        set_vr_monitor_mode(*VR_MONITOR_MODE)   # Windows forgets a turned-off monitor's size and scaling
        prepare_views()
        ensure_headset_hz()
    return changed


def view_kinds():
    return [d.kind for d in list_displays() if d.kind.startswith("view")]


def mouse_area(lock="shown", shown="main"):
    """Rectangle the mouse may use, never the headset's screen.
    lock "shown": only the monitor shown in VR (you can't lose the mouse); "both": main + VR monitor."""
    displays = list_displays()
    kinds = (shown,) if lock == "shown" else ("main", "vr") + tuple(d.kind for d in displays
                                                                       if d.kind.startswith("view"))
    rects = [d.rect for d in displays if d.kind in kinds]
    if not rects:
        rects = [d.rect for d in displays if d.kind == "main"]
    if not rects:
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)


# -- Windows text scaling (per monitor) via the display-config API --------------------------------------
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("size", ctypes.c_uint32), ("adapterId", _LUID), ("id", ctypes.c_uint32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class _GET_DPI(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("minScaleRel", ctypes.c_int), ("curScaleRel", ctypes.c_int),
                ("maxScaleRel", ctypes.c_int)]


class _SET_DPI(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("scaleRel", ctypes.c_int)]


QDC_ONLY_ACTIVE_PATHS = 0x2
PATH_INFO_SIZE, MODE_INFO_SIZE = 72, 64
GET_SOURCE_NAME, GET_DPI_SCALE, SET_DPI_SCALE = 1, -3, -4   # the DPI ones are undocumented but stable


def _source_of(gdi_name):
    count_paths, count_modes = ctypes.c_uint32(), ctypes.c_uint32()
    user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(count_paths), ctypes.byref(count_modes))
    paths = (ctypes.c_ubyte * (PATH_INFO_SIZE * count_paths.value))()
    modes = (ctypes.c_ubyte * (MODE_INFO_SIZE * count_modes.value))()
    user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(count_paths), paths,
                              ctypes.byref(count_modes), modes, None)
    for i in range(count_paths.value):
        base = i * PATH_INFO_SIZE
        luid = _LUID.from_buffer_copy(bytes(paths[base:base + 8]))
        source_id = ctypes.c_uint32.from_buffer_copy(bytes(paths[base + 8:base + 12])).value
        req = _SOURCE_NAME()
        req.header.type, req.header.size = GET_SOURCE_NAME, ctypes.sizeof(_SOURCE_NAME)
        req.header.adapterId, req.header.id = luid, source_id
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(req)) == 0 and req.viewGdiDeviceName == gdi_name:
            return luid, source_id
    return None


def get_scale(display):
    """Current Windows scale % of a display, or None."""
    source = _source_of(display.name)
    if source is None:
        return None
    get = _GET_DPI()
    get.header.type, get.header.size = GET_DPI_SCALE, ctypes.sizeof(_GET_DPI)
    get.header.adapterId, get.header.id = source
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(get)) != 0:
        return None
    return DPI_STEPS[get.curScaleRel - get.minScaleRel]


def set_scale(display, percent):
    source = _source_of(display.name)
    if source is None:
        return False
    get = _GET_DPI()
    get.header.type, get.header.size = GET_DPI_SCALE, ctypes.sizeof(_GET_DPI)
    get.header.adapterId, get.header.id = source
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(get)) != 0:
        return False
    # Steps are relative to Windows' recommended scale; the lowest step is always 100%.
    recommended_index = -get.minScaleRel
    target_index = min(range(len(DPI_STEPS)), key=lambda i: abs(DPI_STEPS[i] - percent))
    rel = max(get.minScaleRel, min(get.maxScaleRel, target_index - recommended_index))
    req = _SET_DPI()
    req.header.type, req.header.size = SET_DPI_SCALE, ctypes.sizeof(_SET_DPI)
    req.header.adapterId, req.header.id = source
    req.scaleRel = rel
    return user32.DisplayConfigSetDeviceInfo(ctypes.byref(req)) == 0


def set_vr_text_size(level):
    """level 1..len(TEXT_SIZES). Changes only the VR monitor."""
    vr = find("vr")
    if vr is None:
        return "VR monitor not found (install it with tools/install_vr_monitor.ps1)"
    width, height, scale = TEXT_SIZES[max(1, min(len(TEXT_SIZES), int(level))) - 1]
    if (vr.width, vr.height) != (width, height):
        dm = _devmode(vr.name)
        dm.dmPelsWidth, dm.dmPelsHeight = width, height
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
        _change(vr.name, dm)
        place_vr_monitor_left_of_main()
        vr = find("vr")
    ok = set_scale(vr, scale)
    ensure_headset_hz()
    return f"VR monitor: {width}x{height} at {scale}%" + ("" if ok else " (scaling not changed)")


def set_vr_monitor_mode(width, height, scale):
    """Put the VR monitor in a given resolution + Windows scaling (only that monitor changes)."""
    vr = find("vr")
    if vr is None:
        return False
    if (vr.width, vr.height) != (width, height):
        dm = _devmode(vr.name)
        dm.dmPelsWidth, dm.dmPelsHeight = width, height
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
        _change(vr.name, dm)
        place_vr_monitor_left_of_main()
        vr = find("vr")
    if get_scale(vr) != scale:
        set_scale(vr, scale)
    ensure_headset_hz()
    return True


def describe(level):
    w, h, s = TEXT_SIZES[max(1, min(len(TEXT_SIZES), int(level))) - 1]
    factor = (1080 / h) * (s / 100)
    return f"{factor:.1f}x  ({w}x{h}, {s}%)"
