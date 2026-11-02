"""The shell: the VR desktop around you.

Owns the windows (one per entry of settings.panels, each showing an app's Content), the system interface
(title bars, settings cards, dock, music player, dialogs, zoom lens) and the pointer. Every frame `update()`
brings everything up to date and returns the surfaces to draw; `overlay()` adds the pointer dot and lens
after the pointer has handled this frame's input. Render thread only, except where noted.
"""
import math
import threading
import time

from .. import apps
from ..apps.desktop import new_panel_for
from ..core import geometry
from ..core.settings import new_panel
from ..scene.surface import INTERFACE, OVERLAY, Surface
from ..win import displays, start_apps, windows
from ..win.media import MediaWatcher
from . import workspace
from .chrome import BAR_IMAGE_WIDTH, SettingsCard, TitleBar
from .dialogs import DIALOG_DISTANCE, GlobalSettings, ListDialog
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT, Dock, Player
from .pointer import Pointer

BAR_LINGER_SECONDS = 1.5          # a window's title bar stays this long after the pointer leaves the window
CARD_DISTANCE, CARD_SIDE_DEGREES = 1.2, 18   # settings card: near you, a little to the right of its window
OPENED_FROM_VR_SECONDS = 6        # a new app window this soon after a click in VR was opened from VR
LAUNCHED_FROM_VR_SECONDS = 20     # apps can take a while to show their window after launching
LENS_SIZE, LENS_ZOOM = 0.38, 2.0  # zoom lens: width as part of the window, magnification
POINTER_BLUE = (0.31, 0.61, 0.98, 1.0)


class AppHost:
    """What an app may ask of the shell (see apps.base)."""

    def __init__(self, shell):
        self._shell = shell
        self.mover = windows.WindowMover()   # puts app windows on the VR monitor and back

    def settings(self):
        return self._shell.settings()

    def update_panel(self, window_id, **fields):
        self._shell.store.update_window(window_id, **fields)

    def slot_of(self, window_id):
        """A small number per window (staggers them on the VR monitor)."""
        ids = [p["id"] for p in self._shell.settings().panels]
        return ids.index(window_id) if window_id in ids else 0

    def note_click(self):
        self._shell.last_click = time.monotonic()


class _Window:
    def __init__(self, content, shell, window_id):
        self.content = content
        self.bar = TitleBar(shell, window_id)
        self.card = SettingsCard(shell, window_id, CARD_DISTANCE)
        self.target = _WindowTarget(shell, window_id)
        self.surface = None


class _WindowTarget:
    """Pointer input on a window: shell gestures first (Alt + drag moves it, wheel button bends it, Alt +
    wheel = closer / farther), everything else goes to the app."""

    def __init__(self, shell, window_id):
        self.shell, self.window_id = shell, window_id
        self.grab_distance = None

    def on_pointer(self, e):
        shell = self.shell
        if e.kind == "move":
            shell.bar_seen[self.window_id] = time.monotonic()
        if e.kind == "press":
            shell.select(self.window_id)
            if (e.button == "left" and e.alt) or e.button == "middle":
                shell.grab_window(self.window_id, e)
                self.grab_distance = shell.gesture_distance()
                return
        if shell.gesture is not None and e.kind in ("drag", "release", "wheel"):
            {"drag": shell.gesture_drag, "release": shell.gesture_release, "wheel": shell.gesture_wheel}[e.kind](e)
            self.grab_distance = shell.gesture_distance()
            return
        if e.kind == "wheel" and e.alt:
            workspace.nudge_distance(shell.store, self.window_id, e.delta)
            return
        window = shell.windows.get(self.window_id)
        if window is not None:
            window.content.on_pointer(e)


class Shell:
    def __init__(self, store, recenter):
        self.store = store
        self.recenter = recenter                 # callable: make where you look "straight ahead"
        self.host = AppHost(self)
        self.media = MediaWatcher()
        self.pointer = Pointer(store, self._on_shortcut)
        self.pointer.on_press = self._on_press
        self.windows = {}                        # window id -> _Window
        self.bar_seen = {}                       # window id -> last time the pointer was on it
        self.settings_open = set()               # window ids with their settings card open
        self.zoomed = set()                      # window ids with the zoom lens on
        self.gesture = None                      # WindowMove / WindowBend in progress
        self.dialog = None                       # the open dialog view (one at a time)
        self.dock = Dock(self)
        self.player = Player(self)
        self.start_apps = []                     # [(name, app id)] from the Start menu, loaded in the background
        self.last_click = 0.0
        self._launched_at = -100.0
        self._known_windows = None
        self._adopt_checked = 0.0
        self._settings = store.get()
        self.look_yaw = 0.0                      # where you look (degrees, + = right), set by the engine
        self.aspects = {}                        # window id -> picture height / width (for the control panel)
        self._heights = {}                       # window id -> height in m of the shown windows (magnets)

    # -- lifetime --------------------------------------------------------------------------------
    def start(self):
        self.pointer.start()
        self.media.start()
        threading.Thread(target=lambda: setattr(self, "start_apps", start_apps.load_start_apps()), daemon=True,
                         name="load-apps").start()

    def suspend(self):
        """The VR picture stopped (headset off): every app window goes back to the main screen; they come
        back into VR with the next session (the saved windows stay). Render thread, GL still current."""
        self.pointer.running = False
        for wid in list(self.windows):
            self._drop(wid)
        self._close_dialog()
        for view in (self.dock, self.player):
            view.release()
        self.gesture = None
        self.host.mover.back_all()

    def stop(self):
        self.pointer.stop()
        self.media.stop()
        self.host.mover.back_all()

    # -- state -------------------------------------------------------------------------------------
    def settings(self):
        return self._settings

    def panel(self, window_id):
        return next((p for p in self._settings.panels if p["id"] == window_id), None)

    def select(self, window_id):
        index = self.store.index_of(window_id)
        if index is not None and index != self._settings.selected:
            self.store.update(selected=index)

    # -- every frame -------------------------------------------------------------------------------
    def update(self, now):
        """Bring windows and interface up to date; returns this frame's surfaces (without the overlay)."""
        self._settings = s = self.store.get()
        self._sync_windows(s)
        if now - self._adopt_checked > 1.0:
            self._adopt_checked = now
            self._adopt_new_windows(s)
            s = self._settings = self.store.get()
        surfaces = []
        selected = s.panels[s.selected]["id"] if s.panels else None
        heights = {}
        for p in s.panels:
            window = self.windows.get(p["id"])
            if window is None:
                continue
            window.content.update(p, now)
            if not p.get("shown", True):
                window.surface = None
                continue
            aspect = window.content.aspect
            self.aspects[p["id"]] = aspect
            height = p["width"] * aspect
            heights[p["id"]] = height
            model = geometry.placement(p["yaw"], p["height"], p["distance"], p.get("turn", 0.0), p["tilt"],
                                       p.get("roll", 0.0))
            if p["id"] == selected and len(s.panels) > 1:
                surfaces.append(Surface("frame:" + p["id"], model @ geometry.translate(0, 0, -0.01),
                                        p["width"] + 0.06, height + 0.06, p["curvature"], color=POINTER_BLUE))
            window.surface = Surface("window:" + p["id"], model, p["width"], height, p["curvature"],
                                     texture=window.content.texture, opacity=p["opacity"], target=window.target,
                                     data={"window_id": p["id"]})
            surfaces.append(window.surface)
            surfaces += self._chrome(p, window, now)
        self._heights = heights
        surfaces += self._system_ui()
        return surfaces

    def _sync_windows(self, s):
        """A window for every saved panel; windows whose app is gone leave (and their panel too)."""
        ids = {p["id"] for p in s.panels}
        for wid in [w for w in self.windows if w not in ids]:
            self._drop(wid)
        for p in s.panels:
            if p["id"] not in self.windows:
                content = apps.create(p, self.host)
                if content is None or content.closed:
                    if content is not None:
                        content.release()
                    self.store.remove_window(p["id"])
                    continue
                self.windows[p["id"]] = _Window(content, self, p["id"])
        for wid in [w for w, window in self.windows.items() if window.content.closed]:
            self._drop(wid)
            self.store.remove_window(wid)
        self._settings = self.store.get()

    def _drop(self, wid):
        window = self.windows.pop(wid)
        window.content.release()
        window.bar.release()
        window.card.release()
        self.settings_open.discard(wid)
        self.zoomed.discard(wid)

    def _chrome(self, p, window, now):
        """Title bar while you point at the window (or its settings are open), and its settings card."""
        wid = p["id"]
        settings_open = wid in self.settings_open
        if not self.pointer.active or (now - self.bar_seen.get(wid, -10) > BAR_LINGER_SECONDS and not settings_open):
            return []
        out = []
        bar = window.bar
        bar.m_per_px = p["width"] / BAR_IMAGE_WIDTH   # part of the window: it scales with it
        bar.refresh()
        s = window.surface
        model = s.model @ geometry.translate(0, s.height / 2 + bar.height / 2, 0)
        out.append(Surface("bar:" + wid, model, bar.width, bar.height, p["curvature"], texture=bar.texture,
                           layer=INTERFACE, target=_Lingering(self, wid, bar), data={"ui": True}))
        if settings_open:
            card = window.card
            card.refresh()
            model = geometry.rotate_y(-(p["yaw"] + CARD_SIDE_DEGREES)) @ geometry.translate(0, 0, -CARD_DISTANCE)
            out.append(Surface("card:" + wid, model, card.width, card.height, texture=card.texture,
                               layer=INTERFACE, target=card, data={"ui": True}))
        return out

    def _system_ui(self):
        out = []
        self.dock.refresh()
        dock_model = geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ geometry.tilt(DOCK_TILT)
        out.append(Surface("dock", dock_model, self.dock.width, self.dock.height, texture=self.dock.texture,
                           layer=INTERFACE, target=self.dock, data={"ui": True}))
        if self.dialog is None:   # the player makes room for dialogs
            self.player.refresh()
            model = dock_model @ geometry.translate(0, self.dock.height / 2 + 0.02 + self.player.height / 2, 0)
            out.append(Surface("player", model, self.player.width, self.player.height,
                               texture=self.player.texture, layer=INTERFACE, target=self.player, data={"ui": True}))
        else:
            self.dialog.refresh()
            out.append(Surface("dialog", geometry.translate(0, 0, -DIALOG_DISTANCE), self.dialog.width,
                               self.dialog.height, texture=self.dialog.texture, layer=INTERFACE, target=self.dialog,
                               data={"ui": True}))
        return out

    def overlay(self):
        """Pointer dot and zoom lens, after the pointer handled this frame's input."""
        if not self.pointer.active:
            return []
        out = self._lens()
        direction = self.pointer.direction()
        distance = self.pointer.dot_distance()
        end = direction * (distance - 0.01)   # a hair in front of what it is on
        face = (geometry.translate(*end) @ geometry.rotate_y(math.degrees(self.pointer.yaw))
                @ geometry.rotate_x(math.degrees(self.pointer.pitch)))
        size = 0.012 * distance
        out.append(Surface("dot", face @ geometry.scale(size), 1, 1, shape="disc", color=POINTER_BLUE, layer=OVERLAY))
        out.append(Surface("dot-center", face @ geometry.translate(0, 0, 0.001) @ geometry.scale(size * 0.55), 1, 1,
                           shape="disc", color=(1.0, 1.0, 1.0, 1.0), layer=OVERLAY))
        return out

    def _lens(self):
        """Zoom lens: on a window with zoom on, a magnified view of the spot under the pointer, floating just
        in front of it."""
        hit = self.pointer.hit
        if not hit or hit[0].data.get("window_id") not in self.zoomed:
            return []
        s, _, u, v = hit
        if s.texture is None or not s.texture.ready:
            return []
        lw, lh = s.width * LENS_SIZE, s.width * LENS_SIZE * 0.6
        su, sv = lw / (s.width * LENS_ZOOM), lh / (s.height * LENS_ZOOM)
        cu, cv = min(max(u, su / 2), 1 - su / 2), min(max(v, sv / 2), 1 - sv / 2)
        model = s.frame_at(u, v, lift=0.05)
        return [Surface("lens-frame", model @ geometry.translate(0, 0, -0.002), lw + 0.02, lh + 0.02,
                        color=POINTER_BLUE, layer=INTERFACE),
                Surface("lens", model, lw, lh, texture=s.texture, layer=INTERFACE,
                        uv=(cu - su / 2, cv - sv / 2, cu + su / 2, cv + sv / 2))]

    # -- actions (from the interface) --------------------------------------------------------------
    def window_action(self, wid, action):
        p = self.panel(wid)
        if p is None:
            return
        if action == "zoom":
            self.zoomed ^= {wid}
        elif action in ("lock_position", "lock_look"):
            self.store.update_window(wid, **{action: not p.get(action, False)})
        elif action == "settings":
            self.settings_open ^= {wid}
        elif action == "minimize":
            self.store.update_window(wid, shown=False)
            self.settings_open.discard(wid)
        elif action == "close":
            window = self.windows.get(wid)
            if window is not None:
                window.content.request_close()   # the app closes as usual; its window then leaves VR
            self.settings_open.discard(wid)

    def set_window_value(self, wid, key, value):
        self.select(wid)
        self.store.update(**{key: value})   # the selected window (keeps its look when its distance changes)

    def show_window(self, wid):
        self.store.update_window(wid, shown=True)
        self.select(wid)

    def dock_action(self, action):
        if action == "recenter":
            self.recenter()
        elif action == "mouse_to_pc":
            self.pointer.enabled = False
        elif action == "global_settings":
            self._open_dialog(GlobalSettings, lambda: GlobalSettings(self))
        elif action == "apps":
            self._open_dialog("apps", lambda: ListDialog("Open an app in VR", lambda: self.start_apps, self._launch,
                                                         "Loading apps..."))
        elif action == "add_window":
            self._open_dialog("add", self._add_window_dialog)

    def _open_dialog(self, kind, make):
        opening = getattr(self.dialog, "kind", None) != kind
        self._close_dialog()
        if opening:
            self.dialog = make()
            self.dialog.kind = kind

    def _close_dialog(self):
        if self.dialog is not None:
            self.dialog.release()
            self.dialog = None

    def _add_window_dialog(self):
        in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
        entries = [(f"{title}   ({exe})", ("window", hwnd, title, exe))
                   for hwnd, title, exe in windows.list_open_windows() if hwnd not in in_vr]
        in_use = {p["source"] for p in self._settings.panels}
        entries += [(f"Desktop view {k[4:]} (a whole desktop with big text)", ("view", k))
                    for k in displays.view_kinds() if k not in in_use]
        entries.append(("Main monitor (whole screen - for videos, not for reading)", ("view", "main")))
        return ListDialog("Add a window to VR", lambda: entries, self._pick, "No other open windows")

    def _pick(self, choice):
        if choice[0] == "window":
            _, hwnd, title, exe = choice
            panel = new_panel_for(hwnd, title, exe)
        elif choice[1] == "main":
            panel = new_panel("main", "Main monitor")
        else:
            panel = new_panel(choice[1], f"Desktop view {choice[1][4:]}", width=3.0, distance=1.6, curvature=0.2)
        workspace.add_window(self.store, panel, self.look_yaw)
        self._close_dialog()

    def _launch(self, app_id):
        self._close_dialog()
        self._launched_at = time.monotonic()   # its window comes into VR (see _adopt_new_windows)
        try:
            start_apps.launch(app_id)
        except OSError:
            pass

    def _on_press(self, surface):
        """Any press: one into a window or into empty space closes the settings cards and dialogs."""
        if surface is None or not surface.data.get("ui"):
            self.settings_open.clear()
            self._close_dialog()

    def _on_shortcut(self, action):
        if action == "recenter":
            self.recenter()
        elif action == "zoom":
            hit = self.pointer.hit
            wid = hit[0].data.get("window_id") if hit else None
            if wid is None and self._settings.panels:
                wid = self._settings.panels[self._settings.selected]["id"]
            if wid:
                self.zoomed ^= {wid}

    # -- moving / bending windows --------------------------------------------------------------------
    def grab_window(self, wid, e):
        p = self.panel(wid)
        if p is None:
            return
        self.select(wid)
        if e.button == "middle":
            self.gesture = workspace.WindowBend(self.store, wid, e)
        elif e.button == "left" and not p.get("lock_position"):
            self.gesture = workspace.WindowMove(self.store, wid, e, dict(self._heights))

    def gesture_drag(self, e):
        if self.gesture:
            self.gesture.drag(e)

    def gesture_wheel(self, e):
        if self.gesture:
            self.gesture.wheel(e)

    def gesture_release(self, e):
        if self.gesture:
            self.gesture.release(e)
            self.gesture = None

    def gesture_distance(self):
        return getattr(self.gesture, "grab_distance", None)

    # -- app windows opened from VR come into VR ----------------------------------------------------
    def _adopt_new_windows(self, s):
        """An app window that appears shortly after a click in VR (or after launching an app from the dock)
        becomes a VR window. Windows opened from the main screen stay on the main screen."""
        current = {hwnd: (title, exe) for hwnd, title, exe in windows.list_open_windows()}
        now = time.monotonic()
        launched = now - self._launched_at < LAUNCHED_FROM_VR_SECONDS
        clicked = s.auto_add_windows and self.pointer.active and now - self.last_click < OPENED_FROM_VR_SECONDS
        if self._known_windows is not None and (launched or clicked):
            in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
            for hwnd, (title, exe) in current.items():
                if hwnd not in self._known_windows and hwnd not in in_vr:
                    workspace.add_window(self.store, new_panel_for(hwnd, title, exe), self.look_yaw)
        self._known_windows = set(current)


class _Lingering:
    """A title bar's target: pointing at it keeps it shown, like pointing at its window."""

    def __init__(self, shell, window_id, view):
        self.shell, self.window_id, self.view = shell, window_id, view

    def on_pointer(self, e):
        self.shell.bar_seen[self.window_id] = time.monotonic()
        self.view.on_pointer(e)

    @property
    def grab_distance(self):
        return self.shell.gesture_distance()

