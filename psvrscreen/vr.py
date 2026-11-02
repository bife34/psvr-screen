"""VR engine: shows the captured screen as a SteamVR overlay and applies settings live.

With "own room" on and no game running, the app also acts as the scene app (like a game): SteamVR then
closes its own menu and shows our room. When a game starts, SteamVR asks us to step aside and we keep
running as an overlay on top of the game.
"""
import ctypes
import math
import threading
import time

import glfw
import openvr
from OpenGL import GL

from .capture import Capture

OVERLAY_KEY = "psvrscreen.main"
ROOM_COLOR = (0.06, 0.08, 0.11)


class _Restart(Exception):
    pass


def _yaw_matrix(yaw, x, y, z, tilt_deg=0.0):
    """Turn by yaw around the vertical axis, lean back by tilt (degrees), placed at x, y, z."""
    m = openvr.HmdMatrix34_t()
    c, s = math.cos(yaw), math.sin(yaw)
    t = math.radians(-tilt_deg)
    ct, st = math.cos(t), math.sin(t)
    # Ry(yaw) @ Rx(-tilt)
    rows = ((c, s * st, s * ct, x), (0.0, ct, -st, y), (-s, c * st, c * ct, z))
    for r, row in enumerate(rows):
        for col, v in enumerate(row):
            m.m[r][col] = v
    return m


def _desktop_size():
    """Bounds of the whole Windows desktop; changes when monitors are added, removed or moved."""
    m = ctypes.windll.user32.GetSystemMetrics
    return m(76), m(77), m(78), m(79), m(80)   # virtual screen x, y, width, height, monitor count


class FrameGrabber(threading.Thread):
    """Captures the screen at the configured fps, independent of the headset's frame rate."""

    def __init__(self, store, kind=None):
        super().__init__(daemon=True, name="capture")
        self.store = store
        self.kind = kind   # "main" / "vr"; None = follow the `monitor` setting
        self.frame = None
        self.seq = 0
        self.fps = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def latest(self):
        with self._lock:
            return self.seq, self.frame

    def run(self):
        capture = Capture()
        layout = _desktop_size()
        frames, t_fps = 0, time.perf_counter()
        try:
            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                s = self.store.get()
                if _desktop_size() != layout:
                    # Monitors were added, removed or moved: reconnect to the new layout.
                    layout = _desktop_size()
                    capture.close()
                    capture = Capture()
                if self.kind or s.visible:   # a monitor panel always shows (SteamVR mode can hide it)
                    try:
                        frame = capture.grab(self.kind or s.monitor)
                    except Exception:
                        capture.close()
                        capture = Capture()
                        self._stop_event.wait(0.5)
                        continue
                    with self._lock:
                        self.frame = frame
                        self.seq += 1
                    frames += 1
                now = time.perf_counter()
                if now - t_fps >= 1.0:
                    self.fps = frames / (now - t_fps)
                    frames, t_fps = 0, now
                self._stop_event.wait(max(0.0, 1.0 / s.fps - (time.perf_counter() - t0)))
        finally:
            capture.close()


class VREngine(threading.Thread):
    def __init__(self, store):
        super().__init__(daemon=True, name="vr-engine")
        self.store = store
        self.grabber = FrameGrabber(store)
        # Read by the control panel.
        self.status = "Starting..."
        self.aspect = 9 / 16           # height / width of the captured image
        self.look_yaw = 0.0            # where you look, relative to the screen direction (radians)
        self.look_pitch = 0.0
        self._stop_event = threading.Event()
        self._recenter_event = threading.Event()
        self._base = None              # (x, y, z, yaw, pitch) of your head at the last recenter
        self._no_room_until = 0.0      # after a game takes over, don't grab the room back right away

    @property
    def fps(self):
        return self.grabber.fps if self.status.startswith("Running") else 0.0

    def stop(self):
        self._stop_event.set()
        self.grabber.stop()

    def recenter(self):
        self._recenter_event.set()

    def run(self):
        self.grabber.start()
        while not self._stop_event.is_set():
            try:
                self._session()
                continue  # restart right away (mode change or a game took over)
            except openvr.error_code.InitError:
                self.status = "Waiting for SteamVR... (turn on the headset, then start SteamVR)"
            except Exception as e:  # keep the app alive and retry
                self.status = f"VR error: {str(e).splitlines()[0][:150]}. Retrying..."
            self._stop_event.wait(3)

    # -- one connection to SteamVR ----------------------------------------------------------
    def _connect(self):
        """Returns True when connected as the scene app (our own room), False for overlay only."""
        openvr.init(openvr.VRApplication_Overlay)
        if not self._room_allowed():
            return False
        openvr.shutdown()
        openvr.init(openvr.VRApplication_Scene)
        return True

    def _room_allowed(self):
        return (self.store.get().own_room and time.monotonic() >= self._no_room_until
                and openvr.VRApplications().getCurrentSceneProcessId() == 0)

    def _session(self):
        scene = self._connect()
        overlay = handle = window = None
        try:
            vrsys = openvr.VRSystem()
            overlay = openvr.VROverlay()
            compositor = openvr.VRCompositor() if scene else None
            handle = overlay.createOverlay(OVERLAY_KEY, "PSVR Screen")

            if not glfw.init():
                raise RuntimeError("could not start OpenGL")
            glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
            window = glfw.create_window(64, 64, "psvr-screen-gl", None, None)
            glfw.make_context_current(window)
            screen_tex = self._make_texture()
            room_tex = self._make_texture()
            r, g, b = (int(c * 255) for c in ROOM_COLOR)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, 4, 4, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                            bytes([r, g, b, 255] * 16))

            screen_texture = self._vr_texture(screen_tex)
            room_texture = self._vr_texture(room_tex)
            full = openvr.VRTextureBounds_t(0.0, 0.0, 1.0, 1.0)
            flipped = openvr.VRTextureBounds_t(0.0, 1.0, 1.0, 0.0)   # OpenGL rows are bottom-up
            overlay.setOverlayTextureBounds(handle, flipped)

            self._base = None
            self._recenter_event.set()
            applied_version = -1
            shown_seq = -1
            tex_size = None
            own_room = self.store.get().own_room
            self.status = ("Running - in your own VR room" if scene
                           else "Running - floating over SteamVR / your game")

            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                if scene:
                    try:
                        compositor.waitGetPoses(None, None)
                    except openvr.error_code.CompositorError:
                        time.sleep(0.01)
                self._poll_events(vrsys, scene)
                s = self.store.get()
                if s.own_room != own_room:
                    raise _Restart()   # switch between own room and overlay-only
                if not scene and self._room_allowed():
                    raise _Restart()   # no game running (any more): take the room back

                head = self._head_pose(vrsys)
                if self._recenter_event.is_set() and head is not None:
                    self._recenter_event.clear()
                    self._base = head
                    applied_version = -1

                if self.store.version != applied_version:
                    applied_version = self.store.version
                    self._apply(overlay, handle, s)

                seq, frame = self.grabber.latest()
                if s.visible and frame is not None and seq != shown_seq:
                    shown_seq = seq
                    h, w = frame.shape[:2]
                    self.aspect = h / w
                    GL.glBindTexture(GL.GL_TEXTURE_2D, screen_tex)  # SteamVR changes the binding
                    if tex_size != (w, h):
                        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0,
                                        GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, frame)
                        tex_size = (w, h)
                    else:
                        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h,
                                           GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, frame)
                    overlay.setOverlayTexture(handle, screen_texture)

                if scene:
                    try:
                        compositor.submit(openvr.Eye_Left, room_texture, full)
                        compositor.submit(openvr.Eye_Right, room_texture, full)
                    except openvr.error_code.CompositorError:
                        pass
                GL.glFlush()

                if head is not None:
                    self._update_look(head, s)
                if not scene:
                    self._stop_event.wait(max(0.0, 1.0 / 60 - (time.perf_counter() - t0)))
        except _Restart:
            pass
        finally:
            if overlay is not None and handle is not None:
                try:
                    overlay.destroyOverlay(handle)
                except Exception:
                    pass
            if window is not None:
                glfw.destroy_window(window)
            glfw.terminate()
            openvr.shutdown()

    @staticmethod
    def _make_texture():
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        return tex

    @staticmethod
    def _vr_texture(tex):
        t = openvr.Texture_t()
        t.handle = int(tex)
        t.eType = openvr.TextureType_OpenGL
        t.eColorSpace = openvr.ColorSpace_Auto
        return t

    def _poll_events(self, vrsys, scene):
        event = openvr.VREvent_t()
        while vrsys.pollNextEvent(event):
            if event.eventType == openvr.VREvent_Quit:
                vrsys.acknowledgeQuit_Exiting()
                if scene:
                    # A game is starting: hand over the room, keep the screen as an overlay.
                    self.status = "A game is starting - switching to overlay mode..."
                    self._no_room_until = time.monotonic() + 20
                    raise _Restart()
                self.status = "SteamVR closed. Waiting for it to start again..."
                raise _Restart()

    def _head_pose(self, vrsys):
        poses = vrsys.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
        pose = poses[openvr.k_unTrackedDeviceIndex_Hmd]
        if not pose.bPoseIsValid:
            return None
        m = pose.mDeviceToAbsoluteTracking.m
        fx, fy, fz = -m[0][2], -m[1][2], -m[2][2]   # forward is -Z
        yaw = math.atan2(-fx, -fz)
        pitch = math.asin(max(-1.0, min(1.0, fy)))
        return (m[0][3], m[1][3], m[2][3], yaw, pitch)

    def _update_look(self, head, s):
        if s.follow_head or self._base is None:
            self.look_yaw, self.look_pitch = 0.0, 0.0
        else:
            d = head[3] - self._base[3]
            self.look_yaw = math.atan2(math.sin(d), math.cos(d))
            self.look_pitch = head[4]

    def _apply(self, overlay, handle, s):
        overlay.setOverlayWidthInMeters(handle, s.width)
        overlay.setOverlayCurvature(handle, s.curvature)
        overlay.setOverlayAlpha(handle, s.opacity)
        if s.follow_head or self._base is None:
            overlay.setOverlayTransformTrackedDeviceRelative(
                handle, openvr.k_unTrackedDeviceIndex_Hmd, _yaw_matrix(0.0, 0.0, s.height, -s.distance, s.tilt))
        else:
            x, y, z, yaw, _ = self._base
            overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseStanding,
                _yaw_matrix(yaw, x - math.sin(yaw) * s.distance, y + s.height, z - math.cos(yaw) * s.distance,
                            s.tilt))
        if s.visible:
            overlay.showOverlay(handle)
        else:
            overlay.hideOverlay(handle)
