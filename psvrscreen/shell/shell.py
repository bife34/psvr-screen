"""The shell: the VR desktop around you.

Owns the windows (one per entry of settings.panels, each showing an app's Content), the system interface
(title bars, settings cards, dock, music player, dialogs, zoom lens), the pointer and VR videos (vr_video.py:
while one plays it replaces the room and the windows). Every frame `update()`
brings everything up to date and returns the surfaces to draw; `overlay()` adds the pointer dot and lens
after the pointer has handled this frame's input. Render thread only, except where noted.
"""
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

from .. import apps
from ..apps.desktop import new_panel_for
from ..core import geometry
from ..core.log import log_error
from ..core.settings import new_panel, shown_title
from ..scene.surface import INTERFACE, OVERLAY, Surface
from ..ui.theme import Icon
from ..video import files
from ..win import displays, start_apps, windows
from ..win.media import MediaWatcher
from . import workspace
from .chrome import BAR_IMAGE_WIDTH, SettingsCard, TitleBar
from .dialogs import DIALOG_DISTANCE, MENU_DISTANCE, ContextMenu, GlobalSettings, ListDialog
from .dock import DOCK_DISTANCE, DOCK_HEIGHT, DOCK_TILT, Dock, Player
from .home import Home
from .motion import Motion
from .pointer import Pointer
from .vr_video import ImmersiveVideo, VideoDetector, WatchButton, is_browser_video

BAR_LINGER_SECONDS = 1.5          # a window's title bar stays this long after the pointer leaves the window
CARD_DISTANCE, CARD_SIDE_DEGREES = 1.2, 18   # settings card: near you, a little to the right of its window
OPENED_FROM_VR_SECONDS = 6        # a new app window this soon after a click in VR was opened from VR
CONTINUE_FROM_SECONDS = 20       # a video stopped after this and before the end is in Continue watching
CONTINUE_UNTIL = 0.95             # (part of its length: later counts as watched)
CONTINUE_KEEP = 9
LASER_SECONDS = 4.0              # the laser shows this long after the controller last aimed
LASER_HAND = (0.12, -0.38, -0.22)  # where the beam starts: your right hand, from your eyes (m)
LASER_WIDTH = 0.004
LASER_COLOR = (0.45, 0.72, 1.0, 0.6)
LASER_FADE_PART = 0.55            # "fading beam": the part of the way it shows
LASER_FADE_PIECES = 10
LAUNCHED_FROM_VR_SECONDS = 20     # apps can take a while to show their window after launching
LENS_HEIGHT, LENS_ZOOM = 0.23, 2.0  # zoom lens: a strip the window's width, this tall (part of the width); 2x
LENS_LIFT = 0.05                  # meters in front of the window
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

    def play_file(self, path):
        """Play a video file around you (from the Videos app)."""
        self._shell.watch_file(path)

    def view_photo(self, path):
        """Show a picture around you (Photos: 360° / 180° / 3D pictures)."""
        self._shell.watch_photo(path)

    def video_file_menu(self, path, recent=False):
        """Right click on a video file: how to play it (and, in Recent, remove it from there)."""
        self._shell.video_file_menu(path, recent)

    def photo_menu(self, path, open_it, recent=False):
        """Right click on a picture in Photos."""
        self._shell.photo_menu(path, open_it, recent)

    def forget_recent(self, paths):
        """Take a file (or a list of them) off Recent."""
        self._shell.forget_recent(paths)

    def open_file(self, path):
        """Open a file (or folder) in its Windows app; the app's window comes into VR."""
        self._shell.open_file(path)

    def file_menu(self, path):
        """Right click on a file in Files."""
        self._shell.file_menu(path)


class _Window:
    def __init__(self, content, shell, window_id):
        self.content = content
        self.bar = TitleBar(shell, window_id)
        self.bar_target = _Lingering(shell, window_id, self.bar)   # one for good (animations know it by it)
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
        self.motion = Motion()                   # open / close animations
        self.scene_grab = _SceneGrab(store)      # drag empty space: turn the surroundings
        self.menu = None                         # the open right-click menu, and where it is
        self._menu_model = None
        self.dock = Dock(self)
        self.player = Player(self)
        self.start_apps = []                     # [(name, app id)] from the Start menu, loaded in the background
        self.last_click = 0.0
        self._launched_at = -100.0
        self._known_windows = None
        self._adopt_checked = 0.0
        self._settings = store.get()
        self.look_yaw = 0.0                      # where you look (degrees, + = right), set by the engine
        self.look_pitch = 0.0                    # (degrees, + = up)
        self.aspects = {}                        # window id -> picture height / width (for the control panel)
        self.video_detector = VideoDetector()
        self.watch_buttons = {}                  # hwnd -> WatchButton (browser windows showing a VR video)
        self.video = None                        # ImmersiveVideo while one plays
        self.dismissed_videos = set()            # video ids whose "Watch in VR" was closed with its x
        self.dismissed_media = None              # title of the paused media whose player row was closed
        self.played_media = set()                # titles that played during this session (paused ones stay)
        self._opened_file = None                 # a video file's VideoInfo, ready to play (from a thread)

    # -- lifetime --------------------------------------------------------------------------------
    def start(self):
        self.pointer.start()
        self.media.start()
        self.video_detector.start()
        threading.Thread(target=lambda: setattr(self, "start_apps", start_apps.load_start_apps()), daemon=True,
                         name="load-apps").start()

    def suspend(self):
        """The VR picture stopped (headset off): every app window goes back to the main screen; they come
        back into VR with the next session (the saved windows stay). Render thread, GL still current."""
        self.pointer.running = False
        self.stop_video()
        self._release_watch_buttons()
        for wid in list(self.windows):
            self._drop(wid)
        self._close_dialog()
        self.close_menu()
        self.motion.flush()          # frees what waited for its fade-out (the GL context is still current)
        for view in (self.dock, self.player):
            view.release()
        self.gesture = None
        self.host.mover.back_all()

    def stop(self):
        self.pointer.stop()
        self.media.stop()
        self.video_detector.stop()
        # Quitting: nothing shows the invisible monitors any more, so no window may stay on any of them.
        self.host.mover.back_all(("vr",) + tuple(displays.view_kinds()))

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
        """Bring windows and interface up to date; returns this frame's surfaces (without the overlay),
        animated as they come and go."""
        return self.motion.apply(self._surfaces(now), now)

    def _surfaces(self, now):
        self._settings = s = self.store.get()
        self._sync_windows(s)
        if now - self._adopt_checked > 1.0:
            self._adopt_checked = now
            self._adopt_new_windows(s)
            s = self._settings = self.store.get()
            self._find_browser_videos(s)
        if self._opened_file is not None:   # read in the background: start it here, on the render thread
            info, self._opened_file = self._opened_file, None
            self.stop_video()
            self.video = ImmersiveVideo(self, info, None)
        # Presses on empty space: grab the video (it follows the pointer), else the surroundings (turn them)
        self.pointer.background = (self.video.grab if self.video is not None
                                   else self.scene_grab if s.environment else None)
        if self.video is not None:
            return self.video.surfaces(now) + self._menu_surfaces()
        surfaces = []
        selected = s.panels[s.selected]["id"] if s.panels else None
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
            surfaces += self._watch_button(p, window)
        surfaces += self._system_ui()
        return surfaces + self._menu_surfaces()

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
                           layer=INTERFACE, target=window.bar_target, data={"ui": True}))
        if settings_open:
            card = window.card
            card.refresh()
            model = geometry.rotate_y(-(p["yaw"] + CARD_SIDE_DEGREES)) @ geometry.translate(0, 0, -CARD_DISTANCE)
            out.append(Surface("card:" + wid, model, card.width, card.height, texture=card.texture,
                               layer=INTERFACE, target=card, data={"ui": True}))
        return out

    def _watch_button(self, p, window):
        """"Watch in VR" under a browser window that shows a VR video."""
        hwnd = getattr(window.content, "hwnd", None)
        if hwnd not in self._watchable():
            return []
        button = self._watch_view(hwnd, p["distance"], False)
        s = window.surface
        model = s.frame_at(0.5, 1.0, lift=0.02) @ geometry.translate(0, -button.height / 2 - 0.03, 0)
        return [Surface(f"watch:{hwnd}", model, button.width, button.height, texture=button.texture,
                        layer=INTERFACE, target=button, data={"ui": True})]

    def _watch_view(self, hwnd, distance, with_title):
        button = self.watch_buttons.get(hwnd)
        if button is None or button.with_title != with_title:
            if button is not None:
                button.release()
            button = self.watch_buttons[hwnd] = WatchButton(self, hwnd, distance, with_title)
        button.refresh()
        return button

    def _release_watch_buttons(self, keep=()):
        for hwnd in [h for h in self.watch_buttons if h not in keep]:
            self.watch_buttons.pop(hwnd).release()

    def _system_ui(self):
        out = []
        self.dock.refresh()
        dock_model = geometry.translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ geometry.tilt(DOCK_TILT)
        out.append(Surface("dock", dock_model, self.dock.width, self.dock.height, texture=self.dock.texture,
                           layer=INTERFACE, target=self.dock, data={"ui": True}))
        if self.dialog is None:   # the player makes room for dialogs
            model = dock_model @ geometry.translate(0, self.dock.height / 2, 0)
            if self._player_shown():
                self.player.refresh()
                model = model @ geometry.translate(0, 0.02 + self.player.height / 2, 0)
                out.append(Surface("player", model, self.player.width, self.player.height,
                                   texture=self.player.texture, layer=INTERFACE, target=self.player,
                                   data={"ui": True}))
                model = model @ geometry.translate(0, self.player.height / 2, 0)
            in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
            outside = [h for h in self._watchable() if h not in in_vr]
            if outside:   # a VR video in a browser on the main screen: offer it above the player
                button = self._watch_view(outside[0], DOCK_DISTANCE, True)
                model = model @ geometry.translate(0, 0.02 + button.height / 2, 0)
                out.append(Surface("watch-main", model, button.width, button.height, texture=button.texture,
                                   layer=INTERFACE, target=button, data={"ui": True}))
        else:
            self.dialog.refresh()
            out.append(Surface("dialog", geometry.translate(0, 0, -DIALOG_DISTANCE), self.dialog.width,
                               self.dialog.height, texture=self.dialog.texture, layer=INTERFACE, target=self.dialog,
                               data={"ui": True}))
        self._release_watch_buttons(keep=self._watchable())
        return out

    def _watchable(self):
        """{hwnd: VideoInfo} of the VR videos in browsers whose "Watch in VR" wasn't closed."""
        return {h: info for h, info in self.video_detector.found.items() if info.key not in self.dismissed_videos}

    def _player_shown(self):
        """The music player row: for media playing now, or played during this session and paused - not for
        whatever Windows still remembers from before (every session starts fresh). Closed with its x: hidden
        until something plays."""
        playing = self.media.now_playing
        if not playing:
            return False
        if playing["playing"]:
            self.played_media.add(playing["title"])
        elif playing["title"] not in self.played_media:
            return False
        if self.dismissed_media is None:
            return True
        if playing and (playing["playing"] or playing["title"] != self.dismissed_media):
            self.dismissed_media = None
            return True
        return False

    def dismiss_video(self, hwnd):
        info = self.video_detector.found.get(hwnd)
        if info is not None:
            self.dismissed_videos.add(info.key)

    def dismiss_media(self):
        playing = self.media.now_playing
        self.dismissed_media = playing["title"] if playing else ""

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
        style = self._settings.laser_style
        if style != "dot" and time.monotonic() - self.pointer.laser_seen < LASER_SECONDS:
            out += _laser(end, fade=style == "fade", bright=self.pointer.pressing)
        out.append(Surface("dot", face @ geometry.scale(size), 1, 1, shape="disc", color=POINTER_BLUE, layer=OVERLAY))
        out.append(Surface("dot-center", face @ geometry.translate(0, 0, 0.001) @ geometry.scale(size * 0.55), 1, 1,
                           shape="disc", color=(1.0, 1.0, 1.0, 1.0), layer=OVERLAY))
        return out

    def _lens(self):
        """Zoom lens: on a window with zoom on, a magnified strip as wide as the window at the pointer's height,
        floating just in front of it (bent like the window). The magnified part follows the pointer left /
        right, so you read along a line."""
        hit = self.pointer.hit
        if not hit or hit[0].data.get("window_id") not in self.zoomed:
            return []
        s, _, u, v = hit
        if s.texture is None or not s.texture.ready:
            return []
        lw, lh = s.width, s.width * LENS_HEIGHT
        su, sv = 1 / LENS_ZOOM, lh / (s.height * LENS_ZOOM)
        cu, cv = min(max(u, su / 2), 1 - su / 2), min(max(v, sv / 2), 1 - sv / 2)
        model = s.model @ geometry.translate(0, (0.5 - v) * s.height, LENS_LIFT)
        return [Surface("lens-frame", model @ geometry.translate(0, 0, -0.002), lw + 0.02, lh + 0.02, s.curvature,
                        color=POINTER_BLUE, layer=INTERFACE),
                Surface("lens", model, lw, lh, s.curvature, texture=s.texture, layer=INTERFACE,
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
        self.store.update(**{key: value})   # the selected window (distance moves it straight back)

    def show_window(self, wid):
        self.store.update_window(wid, shown=True)
        self.select(wid)

    def dock_action(self, action):
        if action == "home":
            self._open_dialog("home", lambda: Home(self))
        elif action == "recenter":
            self.recenter()
        elif action == "mouse_to_pc":
            self.pointer.enabled = False
        elif action == "global_settings":
            self._open_dialog(GlobalSettings, lambda: GlobalSettings(self))
        elif action == "apps":
            native = [(f"{title}   ·   VR app", "native:" + name) for name, (title, _, _) in apps.NATIVE_APPS.items()]
            self._open_dialog("apps", lambda: ListDialog("Open an app in VR", lambda: native + self.start_apps,
                                                         self._launch, "Loading apps..."))
        elif action == "add_window":
            self._open_dialog("add", self._add_window_dialog)

    def home_action(self, key):
        """A tile on Home."""
        self._close_dialog()
        if key in apps.NATIVE_APPS:
            self.open_native(key)
        elif key == "settings":
            self.dock_action("global_settings")
        else:
            self.dock_action(key)                  # apps, add_window

    def _open_dialog(self, kind, make):
        opening = getattr(self.dialog, "kind", None) != kind
        self._close_dialog()
        if opening:
            self.dialog = make()
            self.dialog.kind = kind

    def _close_dialog(self):
        if self.dialog is not None:
            self.motion.later(self.dialog.release)   # after it faded out
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

    def open_native(self, name):
        """A native VR app in a new window where you look (or the one already open, shown and selected)."""
        existing = next((p for p in self._settings.panels if p["source"] == "app:" + name), None)
        if existing is not None:
            self.show_window(existing["id"])
            return
        title = apps.NATIVE_APPS[name][0]
        workspace.add_window(self.store, new_panel("app:" + name, title, width=2.2, distance=2.0, height=0.1),
                             self.look_yaw)

    def watch_photo(self, path):
        self._close_dialog()

        def read():
            try:
                self._opened_file = files.probe_photo(path)
            except Exception:
                pass

        threading.Thread(target=read, daemon=True, name="open-photo").start()

    def watch_file(self, path, projection=None, stereo=None, start=0.0):
        """Play a video file around you; projection / stereo: as you chose it (else recognised); from `start`
        seconds on."""
        self._close_dialog()

        def read():
            try:
                info = files.probe(path)
            except Exception:
                return
            info.projection = projection or info.projection
            info.stereo = stereo or info.stereo
            info.start = start
            self._opened_file = info

        threading.Thread(target=read, daemon=True, name="open-video").start()

    # -- Continue watching ------------------------------------------------------------------------------
    def remember_position(self, info, projection, stereo, position):
        """A video file stopped: keep where (Home's Continue watching), or forget it if it was watched to the
        end or hardly started. Nothing is kept in private mode."""
        s = self._settings
        if s.private_mode or not s.show_recent or not os.path.isfile(info.video_url or ""):
            return
        path, duration = info.video_url, info.duration
        watching = [w for w in self._settings.watching if os.path.normcase(w["path"]) != os.path.normcase(path)]
        if duration and CONTINUE_FROM_SECONDS < position < duration * CONTINUE_UNTIL:
            watching.insert(0, {"path": path, "position": round(position, 1), "duration": duration,
                                "projection": projection, "stereo": stereo})
        self.store.update(watching=watching[:CONTINUE_KEEP])

    def continue_watching(self):
        """[(Path, entry)] still on the PC, newest first (none in private mode)."""
        s = self._settings
        if s.private_mode or not s.show_recent:
            return []
        return [(Path(w["path"]), w) for w in s.watching if os.path.isfile(w["path"])]

    def resume(self, entry):
        self.watch_file(Path(entry["path"]), entry.get("projection"), entry.get("stereo"),
                        max(0.0, entry["position"] - 3))   # a few seconds back: you know where you are

    def watching_menu(self, entry):
        path = Path(entry["path"])
        self.open_menu(path.name, [
            ("Continue", Icon.PLAY, lambda: self.resume(entry), "flat"),
            ("Play from the start", Icon.PLAY,
             lambda: self.watch_file(path, entry.get("projection"), entry.get("stereo")), "flat"),
            ("Remove from Continue watching", Icon.CLOSE, lambda: self.forget_recent(entry["path"]), "flat")])

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
        if app_id.startswith("native:"):
            self.open_native(app_id[len("native:"):])
            return
        self._launched_at = time.monotonic()   # its window comes into VR (see _adopt_new_windows)
        try:
            start_apps.launch(app_id)
        except OSError:
            pass

    def _on_press(self, surface, button="left"):
        """Any press: one into a window or into empty space closes the settings cards and dialogs; one
        anywhere but on the menu closes the menu. A right click into empty space while a video plays opens
        the video's menu."""
        if self.menu is not None and (surface is None or surface.id != "menu"):
            self.close_menu()
        if surface is None or not surface.data.get("ui"):
            self.settings_open.clear()
            self._close_dialog()
        if surface is None and button == "right" and self.video is not None:
            self.video_menu()

    # -- right-click menus -----------------------------------------------------------------------------
    def open_menu(self, title, items):
        """A menu where the pointer is. items: [(label, icon, action, style)] ("flat" or "danger")."""
        self.close_menu()
        self.menu = ContextMenu(title, items, self.close_menu)
        self.menu.refresh()
        p = self.pointer
        spot = p.direction() * MENU_DISTANCE
        self._menu_model = (geometry.translate(*spot) @ geometry.rotate_y(math.degrees(p.yaw))
                            @ geometry.rotate_x(math.degrees(p.pitch))
                            @ geometry.translate(self.menu.width / 2 + 0.01, -self.menu.height / 2 - 0.01, 0))

    def close_menu(self):
        if self.menu is not None:
            self.motion.later(self.menu.release)
            self.menu = None

    def _menu_surfaces(self):
        if self.menu is None:
            return []
        self.menu.refresh()
        return [Surface("menu", self._menu_model, self.menu.width, self.menu.height, texture=self.menu.texture,
                        layer=OVERLAY, target=self.menu, data={"ui": True})]

    def window_menu(self, wid):
        p = self.panel(wid)
        if p is None:
            return
        shown = p.get("shown", True)
        title = shown_title(p["title"], p.get("exe"), self._settings.private_mode)
        items = [("Minimize" if shown else "Show", Icon.MINIMIZE if shown else Icon.WINDOW,
                  (lambda: self.window_action(wid, "minimize")) if shown else (lambda: self.show_window(wid)), "flat"),
                 ("Window settings", Icon.SETTINGS, lambda: self.window_action(wid, "settings"), "flat"),
                 ("Zoom lens", Icon.ZOOM, lambda: self.window_action(wid, "zoom"), "flat"),
                 ("Unpin" if p.get("lock_position") else "Pin in place", Icon.PIN,
                  lambda: self.window_action(wid, "lock_position"), "flat"),
                 ("Unlock look" if p.get("lock_look") else "Lock look", Icon.LOCKED,
                  lambda: self.window_action(wid, "lock_look"), "flat"),
                 ("Close", Icon.CLOSE, lambda: self.window_action(wid, "close"), "danger")]
        self.open_menu(title, items)

    def forget_recent(self, paths):
        """Off Recent and Continue watching, for good (what you remove stays removed)."""
        paths = [str(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
        gone = {p.lower() for p in paths}
        s = self._settings
        self.store.update(hidden_recent=s.hidden_recent + [p for p in paths if p.lower() not in
                                                             {h.lower() for h in s.hidden_recent}],
                          watching=[w for w in s.watching if w["path"].lower() not in gone])

    def photo_menu(self, path, open_it, recent):
        items = [("Open", Icon.PHOTO, open_it, "flat"),
                 ("View around me", Icon.SPHERE, lambda: self.watch_photo(path), "flat")]
        if recent:
            items.append(("Remove from Recent", Icon.CLOSE, lambda: self.forget_recent(path), "flat"))
        items.append(("Show in its folder (on the PC)", Icon.FOLDER, lambda: files.show_in_folder(path), "flat"))
        self.open_menu(shown_title(path.name, private=self._settings.private_mode), items)

    def video_file_menu(self, path, recent=False):
        """A video in the Videos app: play it, or play it as a kind you choose."""
        def play(projection=None, stereo=None):
            return lambda: self.watch_file(path, projection, stereo)

        title = shown_title(path.name, private=self._settings.private_mode)
        self.open_menu(title, [
            ("Play", Icon.PLAY, play(), "flat"),
            ("Play as 180°  3D side by side", Icon.PLAY, play("equi180", "side_by_side"), "flat"),
            ("Play as 360°", Icon.PLAY, play("equi", "mono"), "flat"),
            ("Play as 360°  3D top / bottom", Icon.PLAY, play("equi", "top_bottom"), "flat"),
            ("Play as fisheye  3D side by side", Icon.PLAY, play("fisheye", "side_by_side"), "flat"),
            ("Play on a screen (flat)", Icon.PLAY, play("screen", "mono"), "flat")] +
            ([("Remove from Recent", Icon.CLOSE, lambda: self.forget_recent(path), "flat")] if recent else []) +
            [("Show in its folder (on the PC)", Icon.FOLDER, lambda: files.show_in_folder(path), "flat")])

    def open_file(self, path):
        self._close_dialog()
        self._launched_at = time.monotonic()   # its window comes into VR (see _adopt_new_windows)
        try:
            os.startfile(str(path))
        except OSError:
            log_error(f"open {path.name}")

    def file_menu(self, path):
        """A file in Files: videos get the play menu; anything else opens in its Windows app."""
        if path.suffix.lower() in files.EXTENSIONS:
            self.video_file_menu(path)
            return
        items = [("Open", Icon.FILE, lambda: self.open_file(path), "flat")]
        if path.suffix.lower() in files.PICTURE_EXTENSIONS:
            items.append(("View around me", Icon.SPHERE, lambda: self.watch_photo(path), "flat"))
        items.append(("Show in its folder (on the PC)", Icon.FOLDER, lambda: files.show_in_folder(path), "flat"))
        self.open_menu(shown_title(path.name, private=self._settings.private_mode), items)

    def video_menu(self):
        video = self.video
        playing = not video.player.paused
        self.open_menu("Video", [
            ("Pause" if playing else "Play", Icon.PAUSE if playing else Icon.PLAY, video.play_pause, "flat"),
            ("Video settings", Icon.SETTINGS, video.toggle_settings, "flat"),
            ("Recenter here (where I look, also up / down)", Icon.RECENTER, video.recenter_here, "flat"),
            ("Straighten (no tilt)", Icon.RECENTER, video.straighten, "flat"),
            ("Mouse → PC", Icon.MOUSE, lambda: self.dock_action("mouse_to_pc"), "flat"),
            ("Exit video", Icon.CLOSE, self.stop_video, "danger")])

    def _on_shortcut(self, action):
        if action == "recenter":
            self.recenter()
        elif action == "private":
            self.store.update(private_mode=not self._settings.private_mode)
        elif action == "zoom":
            hit = self.pointer.hit
            wid = hit[0].data.get("window_id") if hit else None
            if wid is None and self._settings.panels:
                wid = self._settings.panels[self._settings.selected]["id"]
            if wid:
                self.zoomed ^= {wid}

    # -- VR videos -----------------------------------------------------------------------------------
    def _find_browser_videos(self, s):
        """Tell the detector which windows are browsers on YouTube (checked once a second)."""
        self.video_detector.candidates = [(hwnd, title) for hwnd, title, exe in windows.list_open_windows()
                                          if is_browser_video(exe, title)]

    def watch_video(self, hwnd):
        info = self.video_detector.found.get(hwnd)
        if info is None:
            return
        self.stop_video()
        if info.source == "stream":
            self.media.command("pause")  # the browser stops playing it (a window source keeps playing there)
        self.settings_open.clear()
        self._close_dialog()
        self.video = ImmersiveVideo(self, info, hwnd)

    def stop_video(self):
        """Back to the windows. Render thread (frees the video picture)."""
        if self.video is not None:
            self.video.close()
            self.video = None

    def recentered(self):
        """Where you look became straight ahead (render thread)."""
        if self.video is not None:
            self.video.recentered()

    # -- moving / bending windows --------------------------------------------------------------------
    def grab_window(self, wid, e):
        p = self.panel(wid)
        if p is None:
            return
        self.select(wid)
        if e.button == "middle":
            self.gesture = workspace.WindowBend(self.store, wid, e)
        elif e.button == "left" and not p.get("lock_position"):
            self.gesture = workspace.WindowMove(self.store, wid, e)

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
        if self._known_windows is not None:
            # A new window that opened on the invisible VR monitor by itself (Windows opens it where the app's
            # last window was) can only be seen in VR: it always comes in.
            new = [hwnd for hwnd in current if hwnd not in self._known_windows]
            on_vr = set(windows.windows_on(("vr",))) if new else set()
            in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
            for hwnd in new:
                if hwnd not in in_vr and (launched or clicked or hwnd in on_vr):
                    title, exe = current[hwnd]
                    workspace.add_window(self.store, new_panel_for(hwnd, title, exe), self.look_yaw)
        self._known_windows = set(current)


def _beam(key, start, end, width, color):
    """A thin flat strip from start to end, turned to face your eyes."""
    along = end - start
    length = float(np.linalg.norm(along))
    y = along / length
    middle = (start + end) / 2
    z = -middle / np.linalg.norm(middle)             # toward your eyes
    x = np.cross(y, z)
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    model = np.eye(4, dtype=np.float32)
    model[:3, 0], model[:3, 1], model[:3, 2], model[:3, 3] = x, y, z, middle
    return Surface(key, model, width, length, color=color, layer=OVERLAY)


def _laser(end, fade=False, bright=False):
    """The laser from your right hand (low, in front of you) to the pointer dot: a glow with a bright core.
    fade: it fades out on the way (only the start shows the direction). bright: a button is held."""
    start = np.array(LASER_HAND)
    r, g, b, a = LASER_COLOR
    a = min(1.0, a * (1.6 if bright else 1.0))
    if not fade:
        return [_beam("laser-glow", start, end, LASER_WIDTH * 3, (r, g, b, a * 0.25)),
                _beam("laser", start, end, LASER_WIDTH, (r, g, b, a))]
    out = []
    for i in range(LASER_FADE_PIECES):               # pieces along the first part, each fainter
        t0, t1 = i / LASER_FADE_PIECES * LASER_FADE_PART, (i + 1) / LASER_FADE_PIECES * LASER_FADE_PART
        k = 1.0 - i / LASER_FADE_PIECES
        p0, p1 = start + (end - start) * t0, start + (end - start) * t1
        out.append(_beam(f"laser-glow{i}", p0, p1, LASER_WIDTH * 3, (r, g, b, a * 0.25 * k)))
        out.append(_beam(f"laser{i}", p0, p1, LASER_WIDTH, (r, g, b, a * k)))
    return out


class _SceneGrab:
    """Left button on empty space + drag left / right: the surroundings turn with the pointer, so what you
    grabbed stays under it."""

    def __init__(self, store):
        self.store = store
        self._start = None

    def on_pointer(self, e):
        if e.kind == "press" and e.button == "left":
            self._start = self.store.get().environment_turn
        elif e.kind == "drag" and self._start is not None:
            turn = self._start - (e.yaw - e.start_yaw)
            self.store.update(environment_turn=(turn + 180.0) % 360.0 - 180.0)
        elif e.kind == "release":
            self._start = None


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

