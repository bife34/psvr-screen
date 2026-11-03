"""The shell: the VR desktop around you.

Owns the windows (one per entry of settings.panels, each showing an app's Content), the system interface
(title bars, settings cards, dock, music player, dialogs, zoom lens), the pointer and VR videos (vr_video.py:
while one plays it replaces the room and the windows). Every frame `update()`
brings everything up to date and returns the surfaces to draw; `overlay()` adds the pointer dot and lens
after the pointer has handled this frame's input. Render thread only, except where noted.
"""
import math
import os
import shutil
import queue
import threading
import time
from pathlib import Path

import numpy as np

from .. import apps
from ..apps.desktop import new_game_panel, new_panel_for
from ..core import geometry
from ..core.log import log_error
from ..core.settings import new_panel, shown_title
from ..scene.surface import BLACK, INTERFACE, OVERLAY, Surface
from ..ui.theme import Icon
from ..video import files
from ..win import displays, integrated, start_apps, steamvr, windows
from ..win.media import MediaWatcher
from . import workspace
from .chrome import BAR_IMAGE_WIDTH, SettingsCard, TitleBar
from .dialogs import DIALOG_DISTANCE, MENU_DISTANCE, ContextMenu, DragChip, GlobalSettings, ListDialog, RingMenu
from .dock import DOCK_DISTANCE, Dock, Player, dock_model
from .home import Home
from .motion import Motion
from .pointer import Pointer
from . import assistant, file_ops, voice_commands
from .photo_wall import PhotoWall
from .rooms import Keypad, RecoveryKey, RoomDesigner, Rooms
from .timeline import TimelineRibbon
from .toast import ToastView
from .vr_video import ImmersiveVideo, VideoDetector, WatchButton, is_browser_video

BAR_LINGER_SECONDS = 1.5          # a window's title bar stays this long after the pointer leaves the window
CARD_DISTANCE, CARD_SIDE_DEGREES = 1.2, 18   # settings card: near you, a little to the right of its window
OPENED_FROM_VR_SECONDS = 6        # a new app window this soon after a click in VR was opened from VR
SCREEN_WIDTH, SCREEN_DISTANCE = 4.0, 2.2   # the Screen layouts' desktop: about 85 degrees wide
GAME_START_SECONDS = 180         # a game's window may take this long to appear (launchers, loading)
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
SPACE_SKY = "Deep space"          # the photo wall is out in space: only stars around you (environments/)



def models_status(group):
    from ..ai import models
    return models.status.get(group, "starting")


class AppHost:
    """What an app may ask of the shell (see apps.base)."""

    def __init__(self, shell):
        self._shell = shell
        self.mover = windows.WindowMover()   # puts app windows on the VR monitor and back

    def settings(self):
        return self._shell.settings()

    def update_panel(self, window_id, **fields):
        self._shell.store.update_window(window_id, **fields)

    def set_setting(self, key, value):
        self._shell.store.update(**{key: value})

    def slot_of(self, window_id):
        """A small number per window (staggers them on the VR monitor)."""
        ids = [p["id"] for p in self._shell.settings().panels]
        return ids.index(window_id) if window_id in ids else 0

    def note_click(self):
        self._shell.last_click = time.monotonic()

    def toast(self, text):
        self._shell.toast(text)

    def play_file(self, path):
        """Play a video file around you (from the Videos app)."""
        self._shell.watch_file(path)

    def view_photo(self, path, album=()):
        """Show a picture around you (Photos: 360° / 180° / 3D pictures); `album`: what Previous / Next go
        through."""
        self._shell.watch_photo(path, album)

    def view_wall(self, folder):
        """A folder's pictures and videos around you, side by side (Photos)."""
        self._shell.watch_wall(folder)

    def rooms_menu(self):
        """Memory rooms saved from photo walls."""
        self._shell.rooms_menu()

    def video_file_menu(self, path, recent=False):
        """Right click on a video file: how to play it (and, in Recent, remove it from there)."""
        self._shell.video_file_menu(path, recent)

    def photo_menu(self, path, open_it, recent=False):
        """Right click on a picture in Photos."""
        self._shell.photo_menu(path, open_it, recent)

    def forget_recent(self, paths):
        """Take a file (or a list of them) off Recent."""
        self._shell.forget_recent(paths)

    def start_game(self, game):
        """Start a game (Games app): its window comes into VR as a big screen."""
        self._shell.start_game(game)

    def game_menu(self, game):
        """A game's choices: on a big screen here, or in VR through SteamVR."""
        self._shell.game_menu(game)

    def open_steamvr(self):
        """Hand the headset to SteamVR (its home / dashboard)."""
        self._shell.to_steamvr(steamvr.start)

    def open_file(self, path):
        """Open a file (or folder) in its Windows app; the app's window comes into VR."""
        self._shell.open_file(path)

    def file_menu(self, path):
        """Right click on a file in Files."""
        self._shell.file_menu(path)

    def folder_menu(self, path, window_id=None):
        """Right click on a folder in Files."""
        self._shell.folder_menu(path, window_id)

    def room_safe(self):
        """The locked room's safe you're in (core.cryptomator.Vault), or None."""
        return self._shell.rooms.safe

    def start_file_drag(self, path, window_id):
        """A file / folder pulled out of a Files window: it follows the pointer until let go."""
        self._shell.start_file_drag(path, window_id)

    def open_folder_window(self, path, app="files"):
        """A window of its own for this folder (Files, Photos or Videos), where you look."""
        self._shell.open_folder_window(path, app)


class _Window:
    def __init__(self, content, shell, window_id):
        self.content = content
        self.bar = TitleBar(shell, window_id)
        self.bar_target = _Lingering(shell, window_id, self.bar)   # one for good (animations know it by it)
        self.card = SettingsCard(shell, window_id, CARD_DISTANCE)
        self.target = _WindowTarget(shell, window_id)
        self.surface = None
        self.corner = None   # _ResizeCorner (made when first shown)


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
        self.wall = None                         # PhotoWall while one shows (under a picture opened from it)
        self._opened_wall = None                 # (folder, question, room) to show as a photo wall next frame
        self.timeline = None                     # TimelineRibbon while one shows
        self._opened_timeline = None             # a video file to show as a timeline next frame
        self.voice = None                        # ai.voice.VoiceListener while voice commands are on
        self.brain = None                        # ai.brain.Brain: the assistant (from the first Talk on)
        self._mic_blocked = False                # the microphone gave only silence (Windows asking / said no)
        self.file_drag = None                    # (path, from window id) while a file is dragged between windows
        self._drag_chip = None
        self.quit_request = None                 # "off" / "restart": the dock's power button (main thread acts)
        self._heard = queue.Queue()              # ("voice" / "ask", words) from other threads
        self.toast_view = ToastView(self)        # short messages ("Saved", what you said...)
        self._later = queue.Queue()              # functions from other threads, run on the render thread
        self.rooms = Rooms(self)                 # your rooms, and the locked ones
        self.dismissed_videos = set()            # video ids whose "Watch in VR" was closed with its x
        self.dismissed_media = None              # title of the paused media whose player row was closed
        self.played_media = set()                # titles that played during this session (paused ones stay)
        self._game = None                        # (Game, when started) while its window is awaited
        self.to_steamvr = lambda then=None: None  # set by the engine: let go of the headset for SteamVR
        self.game_windows = set()                # hwnds of games in VR (read by the controller threads)
        self._enter_game = False                 # a click on a game: the controls go to it after the click
        self._opened_file = None                 # a video file's VideoInfo, ready to play (from a thread)
        self._awaiting = None                    # (program key, when): a Windows program opened from Home

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
        self.stop_wall()
        self.stop_timeline()
        self.toast_view.release()
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
        if self.voice is not None:
            self.voice.stop()
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
        return self.motion.apply(self._surfaces(now) + self.toast_view.surfaces(now), now)

    def _surfaces(self, now):
        self._settings = s = self.store.get()
        if self.gesture is not None and not self.pointer.pressing:
            # a move / bend belongs to a press: without one it's left over (its release went elsewhere) and
            # would move that window with every scroll or drag on any window
            self.gesture = None
        self._sync_windows(s)
        self.game_windows = {getattr(self.windows[p["id"]].content, "hwnd", None)
                             for p in s.panels if p.get("game") and p["id"] in self.windows}
        if self._enter_game and not self.pointer.pressing:      # the click into the game is done: play
            self._enter_game = False
            window = next((w for w in self.windows.values() if getattr(w.content, "hwnd", None) in self.game_windows
                           and w.surface is not None and self.pointer.hit and self.pointer.hit[0].id == w.surface.id),
                          None)
            if window is not None:
                self.play_game(window.content.hwnd)
        if now - self._adopt_checked > 1.0:
            self._adopt_checked = now
            self._adopt_new_windows(s)
            s = self._settings = self.store.get()
            self._find_browser_videos(s)
        if self._opened_file is not None:   # read in the background: start it here, on the render thread
            info, self._opened_file = self._opened_file, None
            self.stop_video()
            self.video = ImmersiveVideo(self, info, None)
        if self._opened_wall is not None:
            (folder, question, room), self._opened_wall = self._opened_wall, None
            self.stop_video()
            self.stop_timeline()
            if self.wall is not None and self.wall.folder == Path(folder) and room is None:
                self.wall.ask(question) if question else None
            else:
                self.stop_wall()
                self.wall = PhotoWall(self, folder, room=room)
                if question:
                    self.wall.ask(question)
        if self._opened_timeline is not None:
            path, self._opened_timeline = self._opened_timeline, None
            self.stop_video()
            self.stop_timeline()
            self.timeline = TimelineRibbon(self, path)
        self._listen(s, now)
        while not self._later.empty():
            try:
                self._later.get_nowait()()
            except Exception:
                log_error("later")
        # Presses on empty space: grab the video / photo wall (it follows the pointer), else the surroundings
        self.pointer.background = (self.video.grab if self.video is not None
                                   else self.timeline.grab if self.timeline is not None
                                   else self.wall.grab if self.wall is not None
                                   else self.scene_grab if s.environment and s.scene_grab else None)
        if self.video is not None:
            return self.video.surfaces(now) + self._menu_surfaces()
        if self.timeline is not None:
            return self.timeline.surfaces(now) + self._menu_surfaces()
        if self.wall is not None:
            return self.wall.surfaces(now) + self._menu_surfaces()
        surfaces = []
        selected = s.panels[s.selected]["id"] if s.panels else None
        only = self._screen_panel_id(s) if s.layout != "space" else None
        for p in s.panels:
            window = self.windows.get(p["id"])
            if window is None:
                continue
            if only is not None and p["id"] != only:   # a Screen layout: only its desktop shows
                window.surface = None
                continue
            window.content.update(p, now)
            if not p.get("shown", True):
                window.surface = None
                continue
            aspect = window.content.aspect
            self.aspects[p["id"]] = aspect
            height = p["width"] * aspect
            model = geometry.window_model(p)
            moved = window.content.offset(now)
            if moved is not None:
                model = model @ moved
            if p["id"] == selected and len(s.panels) > 1:
                surfaces.append(Surface("frame:" + p["id"], model @ geometry.translate(0, 0, -0.01),
                                        p["width"] + 0.06, height + 0.06, p["curvature"], color=POINTER_BLUE))
            window.surface = Surface("window:" + p["id"], model, p["width"], height, p["curvature"],
                                     texture=window.content.texture, opacity=p["opacity"], target=window.target,
                                     data={"window_id": p["id"]})
            surfaces.append(window.surface)
            surfaces += window.content.extra_surfaces(p, model, height, now)
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

    def _resize_corner(self, p, window):
        """The resize corner at the window's bottom right (while its title bar shows)."""
        s = window.surface
        size = max(0.05, min(0.14, p["width"] * 0.045))
        spot = s.point(1.0, 1.0, lift=0.015)
        yaw = math.degrees(math.atan2(-spot[0], -spot[2]))       # facing you
        model = geometry.translate(*spot) @ geometry.rotate_y(yaw) @ geometry.translate(-size * 0.35, size * 0.35, 0)
        if window.corner is None:
            window.corner = _ResizeCorner(self, p["id"])
        return Surface("corner:" + p["id"], model, size, size, texture=_corner_texture(), layer=INTERFACE,
                       target=window.corner, data={"ui": True})

    def _chrome(self, p, window, now):
        """Title bar while you point at the window (or its settings are open), and its settings card."""
        wid = p["id"]
        settings_open = wid in self.settings_open
        resizing = window.corner is not None and window.corner._start is not None   # (stays while dragged)
        if not self.pointer.active or (now - self.bar_seen.get(wid, -10) > BAR_LINGER_SECONDS and not settings_open
                                       and not resizing):
            return []
        out = []
        bar = window.bar
        bar.m_per_px = p["width"] / BAR_IMAGE_WIDTH   # part of the window: it scales with it
        bar.refresh()
        s = window.surface
        model = s.model @ geometry.translate(0, s.height / 2 + bar.height / 2, 0)
        out.append(Surface("bar:" + wid, model, bar.width, bar.height, p["curvature"], texture=bar.texture,
                           layer=INTERFACE, target=window.bar_target, data={"ui": True}))
        if not p.get("lock_position"):
            out.append(self._resize_corner(p, window))
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
        dock_at = dock_model(self._settings)
        out.append(Surface("dock", dock_at, self.dock.width, self.dock.height, texture=self.dock.texture,
                           layer=INTERFACE, target=self.dock, data={"ui": True}))
        if self.dialog is None:   # the player makes room for dialogs
            model = dock_at @ geometry.translate(0, self.dock.height / 2, 0)
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
            self.file_drag = None
            return []
        out = self._lens() + self._dragged_file()
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
        if self._settings.layout != "space" and wid != self._screen_panel_id(self._settings):
            self.set_layout("space")                 # a window from the dock: back to your windows around you
        self.store.update_window(wid, shown=True)
        self.select(wid)

    def dock_action(self, action):
        if action == "home":
            self._open_dialog("home", lambda: Home(self))
        elif action == "recenter":
            self.recenter()
        elif action == "mouse_to_pc":
            self.pointer.enabled = False
        elif action == "listen":
            self.listen()
        elif action == "power":
            self.open_menu("PSVR Screen", [
                ("Restart PSVR Screen", Icon.RECENTER, lambda: self.quit("restart"), "flat"),
                ("Turn off PSVR Screen", Icon.POWER, lambda: self.quit("off"), "danger")])
        elif action == "global_settings":
            self._open_dialog(GlobalSettings, lambda: GlobalSettings(self))
        elif action in ("apps", "add_window") and not self._settings.windows_apps:
            self.toast("Windows programs in VR are off (Settings > Controls)")
        elif action == "apps":
            native = [(f"{title}   ·   VR app", "native:" + name) for name, (title, _, _) in apps.NATIVE_APPS.items()]
            self._open_dialog("apps", lambda: ListDialog("Open an app in VR", lambda: native + self.start_apps,
                                                         self._launch, "Loading apps..."))
        elif action == "add_window":
            self._open_dialog("add", self._add_window_dialog)
        elif action == "layout":
            self.open_menu("Layout", [
                ("Space - windows around you", Icon.SPHERE, lambda: self.set_layout("space"), "flat"),
                ("Screen - one big desktop in front of you", Icon.MONITOR, lambda: self.set_layout("screen"), "flat"),
                ("Screen that follows your head (like a plain monitor)", Icon.MONITOR,
                 lambda: self.set_layout("screen_head"), "flat")])

    # -- layouts and the dock -------------------------------------------------------------------------------
    def screen_source(self):
        """The desktop the Screen layouts show: the 4K screen if there is one, else a Desktop view, else the
        main monitor."""
        views = displays.view_kinds()
        return "view2" if "view2" in views else (views[0] if views else "main")

    def set_layout(self, layout):
        """Space: your windows around you. Screen: only one big desktop in front of you (made if needed), apps
        opened from VR go onto it; screen_head: it stays in front of your eyes wherever you look."""
        if layout != "space":
            source = self.screen_source()
            if not any(p["source"] == source for p in self._settings.panels):
                title = displays.view_title(source) if source != "main" else "Main monitor"
                panel = new_panel(source, title, width=SCREEN_WIDTH, distance=SCREEN_DISTANCE, height=0.0,
                                  curvature=0.05)
                self.store.add_panel(panel, self.look_yaw)   # just placed: nothing else moves
        self.store.update(layout=layout, follow_head=layout == "screen_head")

    def _screen_panel_id(self, s):
        source = self.screen_source()
        return next((p["id"] for p in s.panels if p["source"] == source), None)

    def grab_dock(self, e):
        if e.button == "left":
            self.gesture = workspace.DockMove(self.store, e)

    def quit(self, then="off"):
        """Turn the app off ("off") or restart it ("restart"): the control panel closes and the app shuts down
        (windows back to the PC screen, virtual monitors off). Any thread."""
        self.quit_request = then
        self.toast("Restarting..." if then == "restart" else "Turning off...")

    def dock_menu(self):
        self.open_menu("Bottom bar", [("Put the bar back", Icon.RECENTER, lambda: self.store.update(
            dock_yaw=0.0, dock_pitch=-19.3, dock_distance=DOCK_DISTANCE), "flat")])

    def home_action(self, key):
        """A tile on Home."""
        self._close_dialog()
        if key in apps.NATIVE_APPS:
            self.open_native(key)
        elif key in integrated.APPS:
            self.open_integrated(key)
        elif key == "settings":
            self.dock_action("global_settings")
        else:
            self.dock_action(key)                  # apps, add_window

    def open_integrated(self, key):
        """A Windows program that works in VR (win/integrated.py, e.g. Spotify): its window if it's open (already
        in VR: shown), else it's started - or brought back from the tray - and its window comes into VR."""
        app = integrated.APPS[key]
        shown = next((p for p in self._settings.panels if (p.get("exe") or "").lower() == app["exe"]), None)
        if shown is not None:
            self.show_window(shown["id"])
            return
        found = integrated.main_window(key)
        if found is not None:
            hwnd, title, exe = found
            workspace.add_window(self.store, new_panel_for(hwnd, title, exe), self.look_yaw)
            return
        self._awaiting = (key, time.monotonic())
        self.toast(f"Opening {app['name']}...")
        try:
            integrated.launch(key)
        except OSError:
            self._awaiting = None
            self.toast(f"{app['name']} isn't installed on this PC")

    def _open_dialog(self, kind, make):
        opening = getattr(self.dialog, "kind", None) != kind
        self._close_dialog()
        if opening:
            self.dialog = make()
            self.dialog.kind = kind

    def later(self, fn):
        """Run `fn` on the render thread, next frame (from any thread)."""
        self._later.put(fn)

    def close_dialog(self):
        self._close_dialog()

    # -- rooms ----------------------------------------------------------------------------------------
    def rooms_dock_menu(self):
        """The dock's room button: your rooms, a new one, a new locked one (and Lock, inside one)."""
        rooms, s = self.rooms, self._settings
        items = [(("● " if r["id"] == s.current_room and not rooms.secret else "") + r["name"], Icon.HOME,
                  lambda rid=r["id"]: rooms.go(rid), "flat") for r in s.rooms]
        items.append(("Design this room", Icon.SETTINGS, self.design_room, "flat"))
        items.append(("New room", Icon.ADD, rooms.new_room, "flat"))
        items.append(("New locked room", Icon.LOCKED, lambda: self.open_keypad("new"), "flat"))
        if rooms.secret:
            items.insert(0, ("Lock and leave", Icon.LOCKED, rooms.lock, "danger"))
        elif s.current_room != "home":
            items.append(("Delete this room", Icon.CLOSE, lambda: rooms.delete_room(s.current_room), "danger"))
        self.open_menu("Rooms", items)

    def design_room(self):
        """The room designer: this room's name, surroundings and how its windows stand."""
        self._open_dialog(RoomDesigner, lambda: RoomDesigner(self))

    def open_keypad(self, mode, message=""):
        """The PIN pad: "unlock" (any locked room), "new" (make a locked room: PIN twice, then its recovery key)."""
        rooms = self.rooms

        def unlocked(result, pad):
            if result is True:
                self._close_dialog()
            else:
                pad.message = ("Too many tries - wait 30 seconds" if result == "wait" else "That's not it")

        if mode == "unlock":
            pad = Keypad(self, "unlock", lambda code: rooms.try_code(code, lambda r: unlocked(r, pad)), message)
        else:
            def first(pin):
                self._open_dialog("keypad-again", lambda: Keypad(self, "again", lambda again: second(pin, again)))

            def second(pin, again):
                if again != pin:
                    self._close_dialog()
                    self.open_keypad("new", "Not the same - choose again")
                    return
                rooms.create_locked(pin, made)

            def made(recovery):
                self._close_dialog()
                if recovery is None:
                    self.toast("Couldn't make the locked room (see psvr-screen.log)")
                    return
                self._open_dialog("recovery", lambda: RecoveryKey(self, recovery))

            pad = Keypad(self, "new", first, message)
        self._close_dialog()
        self.dialog = pad
        self.dialog.kind = "keypad"

    def _close_dialog(self):
        if self.dialog is not None:
            self.motion.later(self.dialog.release)   # after it faded out
            self.dialog = None

    def _add_window_dialog(self):
        in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
        entries = [(f"{title}   ({exe})", ("window", hwnd, title, exe))
                   for hwnd, title, exe in windows.list_open_windows() if hwnd not in in_vr]
        in_use = {p["source"] for p in self._settings.panels}
        entries += [(displays.view_description(k), ("view", k))
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

    def use_as_surroundings(self, path):
        """A picture becomes the room around you (this room's Surroundings): a 360° picture as it is, a normal
        photo in front of you with the rest of the sphere filled from its own colors (video/room_maker.py)."""
        if files.in_vault(path):
            # the surroundings file isn't encrypted: a picture from the safe would leave it readable on disk
            self.toast("Pictures in the safe can't be surroundings - take it out first")
            return
        self.toast("Making your surroundings...")

        def work():
            from ..video import room_maker
            try:
                name = room_maker.make(path)
            except Exception:
                log_error("use as surroundings")
                self.later(lambda: self.toast("That picture couldn't be used"))
                return
            self.later(lambda: (self.store.update(environment=name, environment_turn=0.0),
                                self.toast("Your picture is the room now")))

        threading.Thread(target=work, daemon=True, name="make-surroundings").start()

    def pick_surroundings(self):
        """Surroundings' "Your picture" card: Photos opens; right click a picture > Use as surroundings."""
        self.close_dialog()
        self.open_native("photos")
        self.toast("Right click a picture > Use as surroundings")

    def forget_surroundings(self, name):
        """One of your own surroundings, gone (the dark room if it was on)."""
        from ..render import environment
        path = environment.path_of(name)
        if path is None or environment.MINE not in path.parents:
            return
        try:
            path.unlink()
        except OSError:
            log_error("remove surroundings")
            return
        environment.forget_preview(name)
        now = self.store.get()
        rooms = [dict(r, environment="") if r.get("environment") == name else r for r in now.rooms]
        changes = {"rooms": rooms}
        if now.environment == name:
            changes["environment"] = ""
        self.store.update(**changes)
        self.toast("Removed")

    def watch_photo(self, path, album=()):
        self._close_dialog()

        def read():
            try:
                info = files.probe_photo(path)
            except Exception:
                return
            info.album = tuple(str(p) for p in album)
            self._opened_file = info

        threading.Thread(target=read, daemon=True, name="open-photo").start()

    def watch_wall(self, folder, question=None, room=None):
        """A folder's pictures and videos side by side around you (the photo wall); `question`: ask it right
        away; `room`: a memory room to open."""
        self._close_dialog()
        self._opened_wall = (folder, question, room)

    def open_timeline(self, path):
        """A video's whole length as a ribbon of frames around you."""
        self._close_dialog()
        self._opened_timeline = Path(path)

    def stop_timeline(self):
        if self.timeline is not None:
            self.timeline.close()
            self.timeline = None

    def rooms_menu(self, wall=None):
        """Memory rooms: save this wall as one, or open a saved one."""
        rooms = self._settings.memory_rooms
        items = []
        if wall is not None:
            items.append(("Save this as a memory room", Icon.STAR, wall.save_room, "flat"))
        for room in reversed(rooms[-10:]):
            items.append((room.get("name", "Room"), Icon.SPHERE,
                          lambda room=room: self.watch_wall(room["folder"], room=room), "flat"))
        if rooms:
            items.append(("Delete a room...", Icon.CLOSE, self._delete_room_menu, "danger"))
        if not items:
            items.append(("No memory rooms yet - open a photo wall and save one", Icon.INFO, lambda: None, "flat"))
        self.open_menu("Memory rooms", items)

    def _delete_room_menu(self):
        rooms = self._settings.memory_rooms
        self.open_menu("Delete which room?", [
            (room.get("name", "Room"), Icon.CLOSE,
             lambda room=room: self.store.update(memory_rooms=[r for r in self._settings.memory_rooms
                                                               if r is not room and r != room]), "danger")
            for room in reversed(rooms[-10:])])

    # -- voice, questions, messages -----------------------------------------------------------------------
    def toast(self, text):
        """A short message in front of you for a few seconds."""
        self.toast_view.show(text)

    def listen(self):
        """Press to talk: the microphone opens for one command (press again to stop)."""
        if self.voice is None:
            self.toast("Voice commands are off (Settings > Controls)")
            return
        s = self._settings
        if s.smart_assistant and self.brain is None:   # the brain wakes up with the first press
            from ..ai.brain import Brain
            self.brain = Brain()
            self.brain.start()
        if self.voice.state in ("downloading", "starting"):
            self.toast("Voice is getting ready..." if self.voice.state == "starting"
                       else "Getting the voice ready (150 MB, once)...")
        elif self.voice.state == "failed":
            self.toast("Voice commands couldn't start (see psvr-screen.log)")
        else:
            self.voice.listen()

    def _listen(self, s, now):
        """Voice commands on / off as Settings says; what was said is done here (render thread): plain
        commands at once, anything else by the assistant's brain (ai/brain.py) when it's there."""
        if s.voice_control and self.voice is None:
            from ..ai.voice import VoiceListener
            self.voice = VoiceListener(lambda words: self._heard.put(("voice", words)))
            self.voice.start()
        elif not s.voice_control and self.voice is not None:
            self.voice.stop()
            self.voice = None
        blocked = self.voice is not None and self.voice.blocked
        if blocked and not self._mic_blocked:
            self._show_pc_screen()   # Windows' permission question is only ever on the PC screen
        self._mic_blocked = blocked
        while not self._heard.empty():
            kind, words = self._heard.get_nowait()
            try:
                if kind == "actions":
                    said, actions = words
                    done = assistant.run(self, actions)
                    self.toast(f"\"{said}\"  ·  {done}" if done else f"\"{said}\"  ·  I'm not sure what to do")
                    continue
                brain = self.brain if s.smart_assistant else None
                thinking = brain is not None and brain.state in ("ready", "loading")
                done = voice_commands.handle(self, words, catch_all=not thinking)
                if done:
                    self.toast(f"\"{words}\"  ·  {done}")
                elif thinking:
                    self.toast(f"\"{words}\"  ·  Thinking...")
                    brain.ask(words, assistant.showing(self),
                              lambda actions, said=words: self._heard.put(("actions", (said, actions))))
                elif brain is not None and brain.state == "downloading":
                    self.toast(f"\"{words}\"  ·  the assistant is still downloading ({models_status('brain')})")
                else:
                    self.toast(f"\"{words}\"  ·  not a command I know")
            except Exception:
                log_error(f"command {words!r}")

    def _show_pc_screen(self):
        """The main monitor in front of you (its window, made if there's none)."""
        main = next((p for p in self._settings.panels if p["source"] == "main"), None)
        if main is not None:
            self.show_window(main["id"])
            return
        workspace.add_window(self.store, new_panel("main", "Main monitor", width=2.4, distance=2.0, height=0.0),
                             self.look_yaw)

    def surroundings(self, s):
        """(scene, brightness, turn) around you now: behind the photo wall plain black (calm: nothing to move
        or sparkle), the stars, or your own - as its settings say; else the one chosen in Settings."""
        if self.wall is not None and self.video is None and s.wall_background != "mine":
            return (SPACE_SKY if s.wall_background == "stars" else BLACK), 1.0, 0.0
        return s.environment, s.environment_brightness, s.environment_turn

    def stop_wall(self):
        """Back to the windows. Render thread (frees the pictures)."""
        if self.wall is not None:
            self.wall.close()
            self.wall = None

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
            panel = new_panel(choice[1], displays.view_title(choice[1]), **displays.view_panel_layout(choice[1]))
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
        if surface is not None and button == "left":
            p = self.panel(surface.data.get("window_id"))
            if p is not None and p.get("game"):
                self._enter_game = True                # clicking a game screen: you play it

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

    def open_ring(self, title, items):
        """The action ring, centred on the pointer (Files: right click on a file or folder)."""
        self.close_menu()
        self.menu = RingMenu(title, items, self.close_menu)
        self.menu.refresh()
        p = self.pointer
        spot = p.direction() * MENU_DISTANCE
        self._menu_model = (geometry.translate(*spot) @ geometry.rotate_y(math.degrees(p.yaw))
                            @ geometry.rotate_x(math.degrees(p.pitch)))

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
                 ("View around me", Icon.SPHERE, lambda: self.watch_photo(path), "flat"),
                 ("Use as surroundings", Icon.GALLERY, lambda: self.use_as_surroundings(path), "flat")]
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
            ("Play on a screen (flat)", Icon.PLAY, play("screen", "mono"), "flat"),
            ("Timeline in space", Icon.HISTORY, lambda: self.open_timeline(path), "flat")] +
            ([("Remove from Recent", Icon.CLOSE, lambda: self.forget_recent(path), "flat")] if recent else []) +
            [("Show in its folder (on the PC)", Icon.FOLDER, lambda: files.show_in_folder(path), "flat")])

    # -- games -----------------------------------------------------------------------------------------------
    def start_game(self, game):
        self._close_dialog()
        self._game = (game, time.monotonic())
        try:
            game.start()
        except OSError:
            log_error(f"start {game.name}")
            self._game = None

    def game_menu(self, game):
        self.open_menu(game.name, [
            ("Play on a big screen (here)", Icon.MONITOR, lambda: self.start_game(game), "flat"),
            ("Play in VR (SteamVR)", Icon.SPHERE, lambda: self.to_steamvr(game.start), "flat")])

    def game_in_front(self):
        """A game in VR has the keyboard and the controls aren't in VR: the DS4 and mouse play the game (any
        thread)."""
        return not self.pointer.active and windows.foreground() in self.game_windows

    def play_game(self, hwnd):
        """Give the controls to a game: the mouse and DS4 leave VR, the game comes to the front."""
        self.pointer.enabled = False
        windows.bring_to_front(hwnd)

    def _game_window_appeared(self, hwnd, title, exe):
        """A new window during a game's start: the game's own (its program is in its folder) becomes the big
        screen and gets the controls; launchers and the like come in as normal windows."""
        game, _ = self._game
        path = windows.exe_path(hwnd).lower()
        if not path.startswith(os.path.normcase(game.folder).lower() + os.sep):
            return False
        workspace.add_window(self.store, new_game_panel(hwnd, title, exe), self.look_yaw)
        self._game = None
        self.play_game(hwnd)
        return True

    def open_file(self, path):
        self._close_dialog()
        self._launched_at = time.monotonic()   # its window comes into VR (see _adopt_new_windows)
        if not self._settings.windows_apps:
            self.toast(f"{Path(path).name} opens on the PC screen")
        try:
            os.startfile(str(path))
        except OSError:
            log_error(f"open {path.name}")

    # -- dragging files between Files windows ----------------------------------------------------------
    def start_file_drag(self, path, window_id):
        self.file_drag = (Path(path), window_id)
        self._drag_chip = DragChip(Path(path))

    def _dragged_file(self):
        """The file being dragged, floating at the pointer; let go: it's dropped on what's under it."""
        if self.file_drag is None:
            return []
        if not self.pointer.pressing:
            self._drop_file()
            return []
        chip = self._drag_chip
        chip.refresh()
        p = self.pointer
        spot = p.direction() * 1.2
        model = (geometry.translate(*spot) @ geometry.rotate_y(math.degrees(p.yaw))
                 @ geometry.rotate_x(math.degrees(p.pitch))
                 @ geometry.translate(chip.width / 2 + 0.02, -chip.height / 2 - 0.02, 0))
        return [Surface("file-drag", model, chip.width, chip.height, texture=chip.texture, layer=OVERLAY)]

    def files_window_under_pointer(self):
        """(window id, its Files page) under the pointer, or (None, None)."""
        hit = self.pointer.hit
        wid = hit[0].data.get("window_id") if hit else None
        window = self.windows.get(wid)
        page = getattr(getattr(window, "content", None), "view", None) if window else None
        return (wid, page) if page is not None and hasattr(page, "dragging") else (None, None)

    def _drop_file(self):
        path, _ = self.file_drag
        self.file_drag = None
        _, page = self.files_window_under_pointer()
        if page is None or page.place == "recent":
            self.toast("Let go over a folder's Files window to move or copy it there")
            return
        target = files.as_path(page.place)
        if target == path.parent or target == path:
            return
        try:
            inside_itself = path.is_dir() and target.is_relative_to(path)
        except TypeError:
            inside_itself = False
        if inside_itself:
            self.toast("A folder can't go inside itself")
            return
        into = files.in_vault(target) and not files.in_vault(path)
        out = files.in_vault(path) and not files.in_vault(target)
        name = "the safe" if files.in_vault(target) and not target.parts else (target.name or str(target))
        moves = "Move into the safe" if into else "Take out here" if out else "Move here"
        copies = "Copy into the safe" if into else "Copy out here" if out else "Copy here"
        self.open_menu(f"{path.name}  ->  {name}", [
            (moves, Icon.LOCKED if into else Icon.FOLDER, lambda: self._file_job("move", path, target), "flat"),
            (copies, Icon.ADD, lambda: self._file_job("copy", path, target), "flat"),
            ("Cancel", Icon.CLOSE, lambda: None, "flat")])

    def _file_job(self, how, path, target):
        """Move / copy in the background (big files take a while), also into / out of the safe (file_ops.py);
        every window showing either folder shows the change."""
        into = files.in_vault(target) and not files.in_vault(path)

        def run():
            try:
                file_ops.transfer(how, path, target)
                done = ("Moved" if how == "move" else "Copied") + f" {path.name}"
                if into and how == "move":
                    done += " into the safe (the original is deleted)"
                self.toast(done)
            except Exception as e:
                log_error(f"{how} {path}")
                self.toast(f"Couldn't {how} {path.name}: {getattr(e, 'strerror', None) or e}")
            self._refresh_folders(path.parent, target)

        self.toast(f"{'Moving' if how == 'move' else 'Copying'} {path.name}...")
        threading.Thread(target=run, daemon=True, name="file-job").start()

    def _refresh_folders(self, *places):
        """Every Files / Photos / Videos window showing one of these folders reads it again."""
        for window in list(self.windows.values()):
            page = getattr(window.content, "view", None)
            if hasattr(page, "dragging") and page.place != "recent" and files.as_path(page.place) in places:
                page.go(page.place)

    def open_folder_window(self, path, app="files"):
        """A new Files (or Photos / Videos) window showing `path` - folders as windows around you, like on a
        desk."""
        title = apps.NATIVE_APPS[app][0]
        name = (path.name or "Safe") if files.in_vault(path) else (Path(path).name or path)
        panel = new_panel("app:" + app, f"{title} · {name}", width=2.2, distance=2.0, height=0.1)
        panel["place"] = str(path)
        workspace.add_window(self.store, panel, self.look_yaw)

    def folder_menu(self, path, window_id=None):
        """A folder in Files: open it here or in its own window, pin it to Places, see it as a photo wall. In the
        safe: no pins or Explorer (both would show it outside the locked room)."""
        path = files.as_path(path)
        items = [("Open", Icon.FOLDER, lambda: self._files_go(window_id, path), "flat"),
                 ("Own window", Icon.ADD, lambda: self.open_folder_window(path, self._app_of(window_id)), "flat")]
        if files.in_vault(path):
            items.append(("Take out", Icon.FOLDER, lambda: self._take_out(path), "flat"))
        else:
            pinned = [str(p) for p in self._settings.pinned_folders]
            if str(path) in pinned:
                pin = ("Unpin", Icon.PIN, lambda: self.store.update(
                    pinned_folders=[p for p in pinned if p != str(path)]), "flat")
            else:
                pin = ("Pin", Icon.PIN, lambda: self.store.update(pinned_folders=pinned + [str(path)]), "flat")
            items += [pin, ("Photo wall", Icon.GALLERY, lambda: self.watch_wall(path), "flat"),
                      ("Explorer", Icon.WINDOW, lambda: self.open_file(path), "flat")]
        if not (files.in_vault(path) and not path.parts):
            items.append(("Delete", Icon.CLOSE, lambda: self._confirm_delete(path), "danger"))
        self.open_ring(path.name or ("Safe" if files.in_vault(path) else str(path)), items)

    def _app_of(self, window_id):
        p = self.panel(window_id) if window_id else None
        source = p["source"] if p else ""
        return source[4:] if source.startswith("app:") and source[4:] in ("files", "photos", "videos") else "files"

    def _files_go(self, window_id, path):
        """The Files window `window_id` goes into `path`."""
        window = self.windows.get(window_id)
        page = getattr(getattr(window, "content", None), "view", None)
        if page is not None and hasattr(page, "dragging"):
            page.go(files.as_path(path))

    def file_menu(self, path):
        """A file in Files: videos get the play menu; anything else opens in its Windows app. In the safe: Take out
        (decrypted into Downloads) instead of the Windows parts."""
        safe = files.in_vault(path)
        if path.suffix.lower() in files.EXTENSIONS and not safe:
            self.video_file_menu(path)
            return
        items = []
        if safe and path.suffix.lower() in files.EXTENSIONS:
            items.append(("Play", Icon.PLAY, lambda: self.watch_file(path), "flat"))
        elif not safe:
            items.append(("Open", Icon.FILE, lambda: self.open_file(path), "flat"))
        if path.suffix.lower() in files.PICTURE_EXTENSIONS:
            items.append(("Around me", Icon.SPHERE, lambda: self.watch_photo(path), "flat"))
            items.append(("Surroundings", Icon.GALLERY, lambda: self.use_as_surroundings(path), "flat"))
        if safe:
            items.append(("Take out", Icon.FOLDER, lambda: self._take_out(path), "flat"))
        else:
            items.append(("Explorer", Icon.FOLDER, lambda: files.show_in_folder(path), "flat"))
        items.append(("Delete", Icon.CLOSE, lambda: self._confirm_delete(path), "danger"))
        self.open_ring(shown_title(path.name, private=self._settings.private_mode or safe), items)

    def _take_out(self, path):
        """A file / folder out of the safe: decrypted into your Downloads (it stays in the safe too)."""
        downloads = next((f for f in files.home_folders() if f.name in ("Downloads", "Transfer\u00eancias")),
                         Path.home())
        self._file_job("copy", path, downloads)

    def _confirm_delete(self, path):
        """Delete = to the Recycle Bin (it can come back), after a yes. In the safe: gone for good."""
        safe = files.in_vault(path)

        def delete():
            try:
                if safe:
                    file_ops.delete_for_good(path)
                    self.toast(f"{path.name} is deleted")
                elif windows.to_recycle_bin(path):
                    self.toast(f"{path.name} is in the Recycle Bin")
                else:
                    self.toast(f"Couldn't delete {path.name}")
            except Exception:
                log_error(f"delete {path}")
                self.toast(f"Couldn't delete {path.name}")
            self._refresh_folders(path.parent)

        self.open_ring(f"Delete {path.name}?", [
            ("Yes, for good" if safe else "Yes, to the Recycle Bin", Icon.CLOSE, delete, "danger"),
            ("No", Icon.BACK, lambda: None, "flat")])

    def video_menu(self):
        video = self.video
        playing = not video.player.paused
        self.open_menu("Video", [
            ("Pause" if playing else "Play", Icon.PAUSE if playing else Icon.PLAY, video.play_pause, "flat"),
            ("Video settings", Icon.SETTINGS, video.toggle_settings, "flat"),
            ("Recenter here (where I look, also up / down)", Icon.RECENTER, video.recenter_here, "flat"),
            ("Straighten (no tilt)", Icon.RECENTER, video.straighten, "flat")] +
            ([("Timeline in space", Icon.HISTORY, lambda: self._video_to_timeline(video), "flat")]
             if video.info.video_url and os.path.isfile(video.info.video_url) else []) + [
            ("Mouse → PC", Icon.MOUSE, lambda: self.dock_action("mouse_to_pc"), "flat"),
            ("Exit video", Icon.CLOSE, self.stop_video, "danger")])

    def _video_to_timeline(self, video):
        path = Path(video.info.video_url)
        self.stop_video()
        self.open_timeline(path)

    def _on_shortcut(self, action):
        if action == "recenter":
            self.recenter()
        elif action == "private":
            self.store.update(private_mode=not self._settings.private_mode)
        elif action == "listen":
            self.listen()
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
        if self.wall is not None:
            self.wall.recentered()
        if self.timeline is not None:
            self.timeline.recentered()

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
        """An app window that appears shortly after a click in VR (or after launching an app from VR) becomes a
        VR window; a game started from VR: its own window, when it comes. Windows opened from the main screen
        stay on the main screen (also ones Windows reopens on the VR monitor)."""
        current = {hwnd: (title, exe) for hwnd, title, exe in windows.list_open_windows()}
        now = time.monotonic()
        if self._game is not None and now - self._game[1] > GAME_START_SECONDS:
            self._game = None
        # Windows programs come into VR only when turned on (Settings > Controls): one by one, as each works
        launched = s.windows_apps and now - self._launched_at < LAUNCHED_FROM_VR_SECONDS
        clicked = (s.windows_apps and s.auto_add_windows and self.pointer.active
                   and now - self.last_click < OPENED_FROM_VR_SECONDS)
        if self._known_windows is not None:
            # A new window on the invisible VR monitor (Windows opens it where the app's last window was): it
            # comes into VR only if you opened it from VR, else it goes to the main screen where you opened it.
            new = [hwnd for hwnd in current if hwnd not in self._known_windows]
            on_vr = set(windows.windows_on(("vr",))) if new else set()
            in_vr = {getattr(w.content, "hwnd", None) for w in self.windows.values()}
            for hwnd in new:
                if hwnd in in_vr:
                    continue
                title, exe = current[hwnd]
                # A game being started: only its own window comes in by itself (not Steam or anything else)
                if self._game is not None and self._game_window_appeared(hwnd, title, exe):
                    continue
                # A program that works in VR, opened from Home: its window comes in (whatever the setting says)
                awaiting = self._awaiting
                if (awaiting and integrated.owns(exe) == awaiting[0]
                        and now - awaiting[1] < LAUNCHED_FROM_VR_SECONDS):
                    self._awaiting = None
                    workspace.add_window(self.store, new_panel_for(hwnd, title, exe), self.look_yaw)
                    continue
                if hwnd in on_vr and not (launched or clicked):
                    windows.to_main(hwnd)
                    continue
                if launched or clicked:
                    if s.layout != "space" and self.screen_source() != "main":
                        windows.move_to_monitor(hwnd, self.screen_source())   # Screen layout: onto its desktop
                        continue
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


class _ResizeCorner:
    """Drag the window's bottom-right corner: it grows / shrinks with the pointer (the middle stays). Apps made
    for VR (Files, Photos, Videos) get more room - more columns and rows, same text size; anything else is a
    picture and scales."""

    def __init__(self, shell, window_id):
        self.shell, self.window_id = shell, window_id
        self._start = None

    def on_pointer(self, e):
        shell = self.shell
        p = shell.panel(self.window_id)
        if p is None:
            return
        window = shell.windows.get(self.window_id)
        content = getattr(window, "content", None)
        if e.kind == "press" and e.button == "left":
            frame = tuple(p.get("frame") or getattr(getattr(content, "view", None), "frame", (1640, 1078)))
            self._start = (p["width"], frame, p["distance"])
            shell.select(self.window_id)
        elif e.kind == "drag" and self._start is not None:
            width0, (fw, fh), distance = self._start
            right = math.radians(e.yaw - e.start_yaw) * distance * 2        # both sides grow: the corner follows
            down = math.radians(e.start_pitch - e.pitch) * distance * 2
            if getattr(content, "resizable", False):
                from ..apps.files import MAX_FRAME, MIN_FRAME
                px_per_m = fw / width0                                      # the same text size
                new_fw = max(MIN_FRAME[0], min(MAX_FRAME[0], int(fw + right * px_per_m)))
                new_fh = max(MIN_FRAME[1], min(MAX_FRAME[1], int(fh + down * px_per_m)))
                shell.store.update_window(self.window_id, frame=[new_fw, new_fh], width=new_fw / px_per_m)
            else:
                grow = right if abs(right) >= abs(down) else down      # a picture: it keeps its shape
                shell.store.update_window(self.window_id, width=max(0.3, width0 + grow))
        elif e.kind == "release":
            self._start = None


_CORNER = None


def _corner_texture():
    """The corner's picture: three short diagonal lines, like a window's grip."""
    global _CORNER
    if _CORNER is None:
        from PIL import Image, ImageDraw
        from ..render.gl import Texture
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 4, 124, 124), 28, fill=(30, 34, 44, 220))
        for k in (36, 64, 92):
            d.line((124 - k + 8, 116, 116, 124 - k + 8), fill=(160, 200, 255, 255), width=9)
        _CORNER = Texture()
        _CORNER.upload(img)
    return _CORNER


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

