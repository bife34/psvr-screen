"""Settings shared by the control panel, the VR engine and the gamepad, saved to settings.json.

The VR space holds several panels (a monitor or a single app window). The panel fields at the top level
(width, distance, ...) always mirror the *selected* panel, so sliders and the gamepad edit that one.
"""
import copy
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_PATH = os.path.join(PROJECT_DIR, "settings.json")

# Keyboard shortcuts (changeable on the Shortcuts tab): action -> (label, default keys)
SHORTCUT_ACTIONS = {
    "mouse_vr": "Mouse into VR / back to the PC screen",
    "zoom": "Zoom lens on the window under the pointer",
    "recenter": "Recenter everything in front of me",
}
DEFAULT_SHORTCUTS = {"mouse_vr": "Ctrl+Alt+P", "zoom": "Ctrl+Alt+Z", "recenter": "Ctrl+Alt+R"}

# Per-panel layout fields
PANEL_KEYS = ("yaw", "width", "distance", "height", "curvature", "tilt", "turn", "roll", "opacity", "text_zoom")
# The two locks on a window's top bar: position lock freezes where it is, settings lock freezes how it looks.
POSITION_KEYS = ("yaw", "distance", "height")
LOOK_KEYS = ("width", "curvature", "tilt", "turn", "roll", "opacity", "text_zoom")
# What an app's profile remembers (not where it is around you, so new windows still spread out)
PROFILE_KEYS = LOOK_KEYS + ("distance", "height", "lock_position", "lock_look", "aspect", "pixels")


@dataclass
class Settings:
    # Selected panel (mirrored from panels[selected])
    yaw: float = 0.0            # degrees around you; - = left, + = right
    width: float = 1.6          # screen width in meters
    distance: float = 1.5       # meters in front of your eyes
    height: float = 0.0         # meters above (+) or below (-) eye level
    curvature: float = 0.1      # 0 = flat, 1 = closed cylinder
    tilt: float = 0.0           # degrees; + leans the top of the screen back, - leans it toward you
    turn: float = 0.0           # degrees; turns the window around its vertical middle (+ = right edge back)
    roll: float = 0.0           # degrees; rotates the window like a picture on a wall (+ = clockwise)
    opacity: float = 1.0
    text_zoom: float = 1.0      # app windows: bigger = everything in the window bigger, panel keeps its size
    # The VR space
    panels: list = field(default_factory=list)   # dicts: source ("main"/"vr"/"window"), title, exe + PANEL_KEYS
    selected: int = 0
    auto_add_windows: bool = True   # while you wear the headset, newly opened app windows go into VR
    sharp_text: bool = True     # big round text: VR monitor at 2560x1440 225%, windows sized for the headset
    shortcuts: dict = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))   # action -> "Ctrl+Alt+P"
    app_profiles: dict = field(default_factory=dict)   # app exe -> its last look (size, text, curve, locks...)
    follow_head: bool = False   # True = stuck to your view, False = fixed in the room
    own_room: bool = True       # act as the main VR app when no game runs (closes the SteamVR menu)
    visible: bool = True
    monitor: str = "main"       # SteamVR mode: "main" or "vr" (the virtual VR monitor)
    vr_text_size: int = 3       # 1..5, see displays.TEXT_SIZES
    fps: int = 30
    mouse_lock: str = "shown"   # "shown" = mouse stays on the monitor shown in VR, "both" = main + VR monitor
    gamepad_mouse: bool = True
    mouse_speed: float = 1.0
    engine: str = "direct"      # "direct" = our own VR without SteamVR, "steamvr" = overlay in SteamVR
    # Lens fine-tuning for direct mode
    lens_fov: float = 95.0      # degrees across one eye's rendered image
    ipd: float = 0.063          # distance between your eyes, meters
    lens_k1: float = 0.22
    lens_k2: float = 0.24
    # VR videos
    video_screen_degrees: float = 60.0   # "screen" videos: how wide the screen looks


# Slider limits, also used by the gamepad so both stay in range.
LIMITS = {
    "yaw": (-170.0, 170.0),
    "width": (0.3, 15.0),
    "distance": (0.5, 8.0),
    "height": (-4.0, 4.0),
    "curvature": (0.0, 0.6),
    "tilt": (-75.0, 75.0),
    "turn": (-80.0, 80.0),
    "roll": (-90.0, 90.0),
    "opacity": (0.2, 1.0),
    "text_zoom": (1.0, 2.5),
    "fps": (10, 60),
    "mouse_speed": (0.2, 3.0),
    "lens_fov": (70.0, 130.0),
    "ipd": (0.055, 0.075),
    "lens_k1": (0.0, 0.6),
    "lens_k2": (0.0, 0.6),
    "video_screen_degrees": (30.0, 100.0),
}


def clamp(name, value):
    lo, hi = LIMITS[name]
    return max(lo, min(hi, value))


def _keep_view_size(panel, changes):
    """Distance only changes how deep a window sits, not how big it looks: when it moves farther away it
    grows by the same factor (and its height moves along), so it covers the same part of your view and its
    text stays just as readable. "Size" alone decides how big it looks."""
    if "distance" not in changes or "width" in changes or panel.get("distance", 0) <= 0:
        return changes
    new_distance = clamp("distance", changes["distance"])
    k = new_distance / panel["distance"]
    changes = dict(changes, distance=new_distance, width=clamp("width", panel["width"] * k))
    if "height" not in changes:
        changes["height"] = clamp("height", panel["height"] * k)
    return changes


def locked(panel, key):
    """Is this field frozen by one of the window's locks?"""
    return (key in POSITION_KEYS and panel.get("lock_position")) or (key in LOOK_KEYS and panel.get("lock_look"))


def new_panel(source, title="", exe="", hwnd=0, **layout):
    panel = {"id": uuid.uuid4().hex[:8], "source": source, "title": title, "exe": exe, "hwnd": hwnd,
             "lock_position": False, "lock_look": False}
    defaults = Settings()
    for key in PANEL_KEYS:
        panel[key] = layout.get(key, getattr(defaults, key))
    return panel


class SettingsStore:
    """Thread-safe holder; `version` increases on every change so readers can spot updates cheaply."""

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self.version = 0
        if not settings.panels:
            # First run / older settings: one panel showing the main monitor with the old layout.
            settings.panels = [new_panel("main", "Main monitor",
                                         **{k: getattr(settings, k) for k in PANEL_KEYS})]
        for p in settings.panels:
            p.setdefault("id", uuid.uuid4().hex[:8])
            p.setdefault("hwnd", 0)
        settings.selected = max(0, min(settings.selected, len(settings.panels) - 1))
        self._mirror_selected()

    @classmethod
    def load(cls):
        settings = Settings()
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(Settings)}
            for key, value in data.items():
                if key in known:
                    setattr(settings, key, value)
            if not isinstance(settings.monitor, str):   # older settings used a number
                settings.monitor = "main"
            settings.shortcuts = {**DEFAULT_SHORTCUTS, **settings.shortcuts}   # new actions get their default
        except (OSError, ValueError):
            pass
        return cls(settings)

    def _mirror_selected(self):
        panel = self._settings.panels[self._settings.selected]
        for key in PANEL_KEYS:
            panel.setdefault(key, getattr(Settings(), key))
            setattr(self._settings, key, panel[key])

    def get(self):
        with self._lock:
            return Settings(**copy.deepcopy(asdict(self._settings)))

    def update(self, **changes):
        with self._lock:
            if self._settings.panels and not locked(self._settings.panels[self._settings.selected], "distance"):
                changes = _keep_view_size(self._settings.panels[self._settings.selected], changes)
            for key, value in changes.items():
                if key in LIMITS:
                    value = clamp(key, value)
                if key in PANEL_KEYS:
                    panel = self._settings.panels[self._settings.selected]
                    if locked(panel, key):
                        continue
                    panel[key] = value
                    self._remember_profile(panel)
                setattr(self._settings, key, value)
            if "selected" in changes:
                self._settings.selected = max(0, min(self._settings.selected, len(self._settings.panels) - 1))
                self._mirror_selected()
            self.version += 1

    def nudge(self, name, delta):
        with self._lock:
            value = clamp(name, getattr(self._settings, name) + delta)
        self.update(**{name: value})

    def _remember_profile(self, panel):
        """Each app keeps its own look: the next time it opens in VR, it comes back the same."""
        if panel.get("source") == "window" and panel.get("exe"):
            self._settings.app_profiles[panel["exe"].lower()] = {k: panel[k] for k in PROFILE_KEYS if k in panel}

    def add_panel(self, panel, center_yaw=0.0, arrange=None):
        """Adds a panel where you look (`center_yaw`, degrees) and selects it - with the app's own profile if
        it was in VR before. `arrange(panels, panel)` may move it and the others to make room (see
        shell.workspace); it runs while the store is locked. Returns its index."""
        with self._lock:
            panels = self._settings.panels
            if panel.get("source") == "window":
                panel.update(self._settings.app_profiles.get((panel.get("exe") or "").lower(), {}))
            panel["yaw"] = clamp("yaw", center_yaw)
            if arrange:
                arrange(panels, panel)
            panels.append(panel)
            self._settings.selected = len(panels) - 1
            self._mirror_selected()
            self.version += 1
            return self._settings.selected

    def update_panel(self, index, **fields_):
        """Change fields of any panel (e.g. shown=False to minimize it)."""
        with self._lock:
            if 0 <= index < len(self._settings.panels):
                panel = self._settings.panels[index]
                if not locked(panel, "distance"):
                    fields_ = _keep_view_size(panel, fields_)
                panel.update({k: (clamp(k, v) if k in LIMITS else v) for k, v in fields_.items()
                              if not locked(panel, k)})
                self._remember_profile(panel)
                self._mirror_selected()
                self.version += 1

    def index_of(self, panel_id):
        with self._lock:
            return next((i for i, p in enumerate(self._settings.panels) if p["id"] == panel_id), None)

    def update_window(self, panel_id, **fields_):
        """update_panel by the window's id (indexes change when windows are added or removed)."""
        index = self.index_of(panel_id)
        if index is not None:
            self.update_panel(index, **fields_)

    def remove_window(self, panel_id):
        index = self.index_of(panel_id)
        return self.remove_panel(index) if index is not None else None

    def remove_panel(self, index):
        with self._lock:
            panels = self._settings.panels
            if not 0 <= index < len(panels):
                return None
            removed = panels.pop(index)
            if not panels:
                panels.append(new_panel("main", "Main monitor"))
            self._settings.selected = max(0, min(self._settings.selected, len(panels) - 1))
            self._mirror_selected()
            self.version += 1
            return removed

    def save(self):
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self.get()), f, indent=2)
