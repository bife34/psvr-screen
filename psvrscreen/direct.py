"""Direct mode: drives the PSVR without SteamVR.

Puts the headset in VR mode, reads head tracking over USB, and renders our own room + screen for each eye
into a fullscreen window on the headset's monitor, with lens correction. On exit the headset goes back to
cinema mode (the normal floating screen).
"""
import ctypes
import math
import os
import threading
import time

import glfw
import numpy as np
from OpenGL import GL

from . import vrui
from .win import displays, start_apps as apps, windows
from .win.media import MediaWatcher
from .pointer import Pointer
from .device.psvr import BUTTON_MIC_MUTE, PSVRControl, PSVRError, PSVRSensors
from .core.settings import new_panel
from .sources import SourceManager
from .device.tracking import HeadTracker, q_to_matrix, yaw_pitch

EYE_W, EYE_H = 960, 1080               # each eye gets half of the 1920x1080 panel
RENDER_SCALE = 1.4                     # render bigger than the panel, the lens correction magnifies the center
FLOOR_Y = -1.2                         # floor below your eyes (seated), meters
WORN_PROXIMITY = 600                   # proximity sensor: ~150 = nothing there, 1023 = headset on your head
OPENED_FROM_VR_SECONDS = 6             # a new window this soon after a click in VR was opened from VR
LAUNCHED_FROM_VR_SECONDS = 20          # apps can take a while to show their window after launching
BAR_LINGER_SECONDS = 1.5               # a window's top bar stays this long after the pointer leaves it
# Dock position: below eye level in front of you, but not so low that it sits in the blurry lens edge
DOCK_HEIGHT, DOCK_DISTANCE, DOCK_TILT = -0.42, 1.2, 28
PICKER_DISTANCE = 1.0                  # dialogs (lists, settings): closer than everything else, so on top
CARD_DISTANCE, CARD_SIDE_DEGREES = 1.2, 18   # a window's settings card: near you, a little right of the window
LENS_SIZE, LENS_ZOOM = 0.38, 2.0       # zoom lens: width as part of the window, magnification
NECK = np.array([0.0, 0.075, -0.08])   # eyes sit above and in front of the neck pivot

# Lens values from PSVRFramework (wiki: Optical characteristics); adjustable in settings.
CHROMA = {"x": (1.0, 1.0091, 1.0224), "y": (1.0, 1.0078, 1.0192)}

SCENE_VS = """
#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
uniform mat4 mvp;
out vec2 v_uv;
out vec3 v_pos;
void main() { v_uv = uv; v_pos = pos; gl_Position = mvp * vec4(pos, 1.0); }
"""
SCREEN_FS = """
#version 330 core
in vec2 v_uv;
uniform sampler2D tex;
uniform float opacity;
out vec4 color;
void main() { color = vec4(texture(tex, v_uv).rgb, opacity); }
"""
LENS_FS = """
#version 330 core
in vec2 v_uv;
uniform sampler2D tex;
uniform vec2 center;     // spot under the pointer in the window picture (0..1)
uniform vec2 span;       // part of the picture the lens shows (0..1)
out vec4 color;
void main() { color = vec4(texture(tex, center + (v_uv - 0.5) * span).rgb, 1.0); }
"""
COLOR_FS = """
#version 330 core
uniform vec4 color;
out vec4 frag;
void main() { frag = color; }
"""
FLOOR_FS = """
#version 330 core
in vec3 v_pos;
out vec4 color;
void main() {
    vec2 g = abs(fract(v_pos.xz * 2.0 - 0.5) - 0.5) / fwidth(v_pos.xz * 2.0);
    float line = 1.0 - min(min(g.x, g.y), 1.0);
    float fade = clamp(1.0 - length(v_pos.xz) / 9.0, 0.0, 1.0);
    vec3 base = vec3(0.045, 0.055, 0.075);
    color = vec4(mix(base, vec3(0.25, 0.42, 0.65), line * 0.6 * fade), 1.0);
}
"""
WARP_VS = """
#version 330 core
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""
WARP_FS = """
#version 330 core
in vec2 v_uv;
uniform sampler2D eye;
uniform vec2 k;          // distortion K1, K2
uniform float aspect;    // eye width / height
uniform float norm;      // keeps the edge of the lens at the edge of the rendered image
uniform vec3 chroma_x;
uniform vec3 chroma_y;
out vec4 color;
vec2 src(vec2 q, float cx, float cy) { return vec2(q.x * cx, q.y * cy * aspect) * 0.5 + 0.5; }
float inside(vec2 uv) { return step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0); }
void main() {
    vec2 p = vec2(v_uv.x * 2.0 - 1.0, (v_uv.y * 2.0 - 1.0) / aspect);
    float r2 = dot(p, p);
    vec2 q = p * (1.0 + k.x * r2 + k.y * r2 * r2) / norm;
    vec2 r = src(q, chroma_x.r, chroma_y.r);
    vec2 g = src(q, chroma_x.g, chroma_y.g);
    vec2 b = src(q, chroma_x.b, chroma_y.b);
    color = vec4(texture(eye, r).r * inside(r), texture(eye, g).g * inside(g), texture(eye, b).b * inside(b), 1.0);
}
"""


def _program(vs, fs):
    def stage(kind, src):
        s = GL.glCreateShader(kind)
        GL.glShaderSource(s, src)
        GL.glCompileShader(s)
        if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(s).decode())
        return s
    p = GL.glCreateProgram()
    GL.glAttachShader(p, stage(GL.GL_VERTEX_SHADER, vs))
    GL.glAttachShader(p, stage(GL.GL_FRAGMENT_SHADER, fs))
    GL.glLinkProgram(p)
    if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(p).decode())
    return p


def _mesh(vertices, indices):
    """vertices: (n, 5) float32 x,y,z,u,v"""
    vao = GL.glGenVertexArrays(1)
    GL.glBindVertexArray(vao)
    vbo, ebo = GL.glGenBuffers(2)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL.GL_STATIC_DRAW)
    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 20, ctypes.c_void_p(0))
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, False, 20, ctypes.c_void_p(12))
    GL.glBindVertexArray(0)
    return vao, (vbo, ebo), len(indices)


def _screen_vertices(width, height, curvature, cols=64):
    """Curved screen centered at the origin, edges bending toward you. v=0 is the top of the picture."""
    verts = []
    radius = width / (2 * math.pi * curvature) if curvature > 0.001 else None
    for i in range(cols + 1):
        u = i / cols
        if radius:
            theta = (u - 0.5) * width / radius
            x, z = radius * math.sin(theta), radius * (1 - math.cos(theta))
        else:
            x, z = (u - 0.5) * width, 0.0
        verts.append((x, height / 2, z, u, 0.0))
        verts.append((x, -height / 2, z, u, 1.0))
    idx = []
    for i in range(cols):
        a = i * 2
        idx += [a, a + 1, a + 2, a + 1, a + 3, a + 2]
    return np.array(verts, np.float32), np.array(idx, np.uint32)


def _perspective(tan_x, tan_y, near=0.05, far=100.0):
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = 1.0 / tan_x
    m[1, 1] = 1.0 / tan_y
    m[2, 2] = -(far + near) / (far - near)
    m[2, 3] = -2 * far * near / (far - near)
    m[3, 2] = -1.0
    return m


def _rigid(rot3, pos):
    m = np.identity(4, np.float32)
    m[:3, :3] = rot3
    m[:3, 3] = pos
    return m


def _translate(x, y, z):
    return _rigid(np.identity(3), (x, y, z))


def _rotate_y(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return _rigid(np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]), (0, 0, 0))


def _rotate_z(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return _rigid(np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]), (0, 0, 0))


def _panel_model(p):
    """Where a window sits: around you (yaw), at its height and distance, then turned, tilted and rolled
    around its own middle."""
    return (_rotate_y(-p["yaw"]) @ _translate(0, p["height"], -p["distance"]) @ _rotate_y(p.get("turn", 0.0))
            @ _tilt(p["tilt"]) @ _rotate_z(-p.get("roll", 0.0)))


def _rotate_x(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return _rigid(np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), (0, 0, 0))


def _disc_vertices(segments=20):
    """Unit disc facing +z (toward you), for the pointer."""
    verts = [(0.0, 0.0, 0.0, 0.5, 0.5)]
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        verts.append((math.cos(a), math.sin(a), 0.0, 0.0, 0.0))
    idx = []
    for i in range(1, segments + 1):
        idx += [0, i, i + 1]
    return np.array(verts, np.float32), np.array(idx, np.uint32)


def _scale(k):
    m = np.identity(4, np.float32)
    m[0, 0] = m[1, 1] = m[2, 2] = k
    return m


GL_TEXTURE_MAX_ANISOTROPY = 0x84FE    # EXT_texture_filter_anisotropic (supported by all desktop GPUs)
GL_TEXTURE_LOD_BIAS = 0x8501
TEXT_SHARPEN_BIAS = -0.4              # pick slightly sharper mipmaps: scaled-down text stays crisper


def _screen_texture():
    """Texture for window pictures and interface images, filtered for readable text: mipmaps against
    shimmering, anisotropic filtering so text on tilted / curved windows stays sharp, and a small bias toward
    the sharper mipmap level."""
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
    GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, 16.0)
    GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_LOD_BIAS, TEXT_SHARPEN_BIAS)
    return tex


def _tilt(degrees):
    """Lean the top of the screen back (+) or toward you (-) around its horizontal middle."""
    t = math.radians(-degrees)
    c, s = math.cos(t), math.sin(t)
    return _rigid(np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), (0, 0, 0))


def _find_headset_monitor():
    monitors = glfw.get_monitors()
    for m in monitors:
        name = (glfw.get_monitor_name(m) or b"").decode(errors="ignore").upper()
        if "PSVR" in name or "SIE" in name:
            return m
    for m in monitors:
        if glfw.get_video_mode(m).refresh_rate in (90, 120):
            return m
    primary = glfw.get_primary_monitor()
    others = [m for m in monitors if glfw.get_monitor_pos(m) != glfw.get_monitor_pos(primary)]
    return others[0] if others else None


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _keep_mouse_off_headset(enable, s=None, worn=False, pointer=False):
    """The headset is a monitor too: keep the real mouse off it. Mouse on the main screen: it stays on the
    main monitor (not on the invisible VR monitor, where you'd lose it). Mouse in VR: the pointer places the
    real cursor on main + VR monitor itself."""
    user32 = ctypes.windll.user32
    if enable:
        rect = _RECT(*(displays.mouse_area("both") if pointer else displays.mouse_area("shown", "main")))
        user32.ClipCursor(ctypes.byref(rect))
    else:
        user32.ClipCursor(None)


class DirectEngine(threading.Thread):
    def __init__(self, store, switch_to_vr_mode=True):
        super().__init__(daemon=True, name="direct-engine")
        self.store = store
        self.switch_to_vr_mode = switch_to_vr_mode   # False = test the picture while the headset stays in cinema mode
        self.sources = SourceManager(store)
        self.pointer = Pointer(store)
        self.pointer.ui_handler = self._on_ui
        self.pointer.shortcut_handler = self._on_shortcut
        self.media = MediaWatcher()
        self.settings_open = set()     # panel ids with their settings card open
        self.picker = None             # window list of the dock's Add button: [(label, hwnd or "main")] or None
        self.global_settings_open = False
        self._picker_windows = {}
        self.zoomed = set()            # panel ids with the zoom lens switched on
        self.apps = []                 # Start menu apps [(name, app id)], loaded in the background
        self.app_list_open = False
        self.list_page = 0
        self._launched_at = -100.0     # when an app was last opened from VR
        self._bar_seen = {}            # panel id -> last time the pointer was on it (shows its top bar)
        self._ui_cache = {}
        self.tracker = HeadTracker()
        # Read by the control panel.
        self.status = "Starting..."
        self.fps = 0.0
        self.aspects = {}              # panel id -> height / width of its picture
        self.aspect = 9 / 16           # of the selected panel
        self.look_yaw = 0.0
        self.look_pitch = 0.0
        self.headset_worn = False
        self._known_windows = None     # app windows that already existed (not auto-added to VR)
        self._stop_event = threading.Event()
        self._recenter_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def recenter(self):
        self._recenter_event.set()

    def run(self):
        self.pointer.start()
        self.media.start()
        threading.Thread(target=lambda: setattr(self, "apps", apps.load_start_apps()), daemon=True,
                         name="load-apps").start()
        try:
            while not self._stop_event.is_set():
                try:
                    self._session()
                except PSVRError as e:
                    self.status = f"{e}. Retrying..."
                except Exception as e:
                    self.status = f"Error: {str(e).splitlines()[0][:150]}. Retrying..."
                self.fps = 0.0
                self._stop_event.wait(3)
        finally:
            self.pointer.running = False
            self.pointer.stop()
            self.media.stop()
            self.sources.stop()   # puts every window back on the main monitor

    def _session(self):
        control = PSVRControl()
        sensors = None
        window = None
        try:
            self.tracker = HeadTracker()
            # Mic-mute button on the headset remote = recenter (doesn't touch Windows at all)
            sensors = PSVRSensors(self.tracker.add_sample,
                                  on_button=lambda bit: self.recenter() if bit == BUTTON_MIC_MUTE else None)
            sensors.start()
            control.power_on()
            time.sleep(0.2)
            self.status = "Calibrating - keep the headset still for a moment..."
            deadline = time.monotonic() + 5
            while not self.tracker.calibrated and time.monotonic() < deadline and not self._stop_event.is_set():
                time.sleep(0.05)
            if not self.tracker.calibrated:
                raise PSVRError("No head tracking data from the headset")

            displays.ensure_headset_hz()
            displays.place_vr_monitor_left_of_main()
            if self.store.get().sharp_text:
                # Many pixels + big scaling on the VR monitor: apps draw big, round text (see window_pixels)
                displays.set_vr_monitor_mode(*windows.VR_MONITOR_MODE)
            displays.prepare_views()
            window = self._open_window()
            if self.switch_to_vr_mode:
                control.vr_mode()
            _keep_mouse_off_headset(True, self.store.get())
            self.status = "Running - direct mode (no SteamVR)"
            self._render_loop(window, sensors)
        finally:
            self.pointer.running = False   # normal mouse back while the VR picture isn't running
            _keep_mouse_off_headset(False)
            if window is not None:
                glfw.destroy_window(window)
                glfw.terminate()
            try:
                control.cinema_mode()
            finally:
                control.close()
            if sensors is not None:
                sensors.stop()

    def _open_window(self):
        if not glfw.init():
            raise RuntimeError("could not start OpenGL")
        monitor = _find_headset_monitor()
        if monitor is None:
            raise PSVRError("Headset screen not found in Windows display settings")
        mode = glfw.get_video_mode(monitor)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.AUTO_ICONIFY, glfw.FALSE)
        glfw.window_hint(glfw.FOCUSED, glfw.FALSE)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
        # Borderless window covering the headset's monitor (not exclusive fullscreen: that triggers the AMD
        # overlay popup and can change the display mode).
        glfw.window_hint(glfw.DECORATED, glfw.FALSE)
        glfw.window_hint(glfw.FLOATING, glfw.TRUE)
        window = glfw.create_window(mode.size.width, mode.size.height, "PSVR Screen - headset", None, None)
        if not window:
            raise RuntimeError("could not open the headset window")
        x, y = glfw.get_monitor_pos(monitor)
        glfw.set_window_pos(window, x, y)
        glfw.make_context_current(window)
        glfw.swap_interval(1)
        glfw.set_input_mode(window, glfw.CURSOR, glfw.CURSOR_HIDDEN)
        return window

    # -- VR interface ------------------------------------------------------------------------
    def _ui_quad(self, uid, key, builder, ui_meshes, distance=None, curvature=0.0, m_per_px=None):
        """Texture + mesh for an interface element, redrawn only when `key` (its content) changes.
        `distance` sizes it by viewing angle (same readability near or far) - or `m_per_px` gives the size
        directly (a window's bar scales with its window); `curvature` bends it like a window."""
        if m_per_px is None:
            m_per_px = vrui.meters_per_px(distance)
        key = (key, round(curvature, 3), round(m_per_px, 6))
        cached = self._ui_cache.get(uid)
        if cached is None or cached["key"] != key:
            img, hotspots = builder()
            tex = cached["texture"] if cached else _screen_texture()
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, img.width, img.height, 0,
                            GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, np.asarray(img, np.uint8))
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            w, h = img.width * m_per_px, img.height * m_per_px
            mkey = (round(w, 3), round(h, 3), round(curvature, 3))
            if mkey not in ui_meshes:
                ui_meshes[mkey] = _mesh(*_screen_vertices(w, h, curvature))
            cached = self._ui_cache[uid] = {"key": key, "texture": tex, "hotspots": hotspots,
                                            "img_w": img.width, "img_h": img.height, "w": w, "h": h,
                                            "curvature": curvature, "mesh": ui_meshes[mkey]}
        return cached

    def _ui_item(self, cached, uid, model, index=None, drag=None):
        return {"ui": True, "id": uid, "model": model, "width": cached["w"], "height": cached["h"],
                "curvature": cached["curvature"], "hotspots": cached["hotspots"], "img_w": cached["img_w"],
                "img_h": cached["img_h"], "texture": cached["texture"], "mesh": cached["mesh"],
                "index": index, "drag": drag}

    def _build_ui(self, s, panels, selected_id, ui_meshes):
        now = time.monotonic()
        hit = self.pointer.hit if self.pointer.active else None
        hover = self.pointer.hotspot() if hit else None
        if hit:
            item = hit[0]
            pid = item.get("panel_id") or (None if item.get("ui") else item["id"])
            if pid:
                self._bar_seen[pid] = now
        items = []
        by_id = {p["id"]: p for p in s.panels}
        for pp in panels:
            pid = pp["id"]
            p = by_id[pid]
            show_bar = now - self._bar_seen.get(pid, -10) < BAR_LINGER_SECONDS or pid in self.settings_open
            if not show_bar or not self.pointer.active:
                continue
            drag = {"yaw": p["yaw"], "elevation": p["height"], "distance": p["distance"],
                    "locked": p.get("lock_position", False)}
            # Top bar: same width, curve and tilt as the window, right on its top edge
            bar_w = p["width"]
            bar_hover = hover[0] if hover and hover[1] == pid else None
            open_ = pid in self.settings_open
            locks = (p.get("lock_position", False), p.get("lock_look", False))
            # The bar is part of the window: it scales with it (size, distance, mouse wheel).
            cached = self._ui_quad("bar:" + pid, (p["title"], round(bar_w, 3), bar_hover, open_, locks,
                                                  pid in self.zoomed),
                                   lambda: vrui.window_bar(p, vrui.BAR_IMAGE_WIDTH, bar_hover, open_,
                                                           pid in self.zoomed),
                                   ui_meshes, m_per_px=bar_w / vrui.BAR_IMAGE_WIDTH, curvature=p["curvature"])
            model = pp["model"] @ _translate(0, pp["height"] / 2 + cached["h"] / 2, 0.0)
            item = self._ui_item(cached, "bar:" + pid, model, pp["index"], drag)
            item["panel_id"] = pid
            items.append(item)
            # Settings card: near you (not out at the window's distance), toward the window, a bit to its side
            if open_:
                values = tuple(round(p.get(k, 0), 3) for k, *_ in vrui.SETTINGS_ROWS) + locks
                dragging = self.pointer.active_slider()
                active_key = (dragging[1] if dragging and dragging[0] == pid
                              else hover[1][1] if hover and hover[0] == "slider" and hover[1][0] == pid else None)
                cached = self._ui_quad("card:" + pid, (values, active_key),
                                       lambda: vrui.settings_card(p, active_key), ui_meshes, CARD_DISTANCE)
                model = _rotate_y(-(p["yaw"] + CARD_SIDE_DEGREES)) @ _translate(0, 0, -CARD_DISTANCE)
                item = self._ui_item(cached, "card:" + pid, model, pp["index"])
                item["panel_id"] = pid
                items.append(item)
        # Dock: always there, fixed below in front of you (straight ahead after recenter), leaning back to face you
        dock_key = (tuple((p["id"], p["title"], p.get("shown", True)) for p in s.panels), selected_id,
                    hover if hover and hover[0] in ("tab", "close", "recenter", "mouse_to_pc", "global_settings",
                                                    "add_menu", "apps_menu") else None)
        dock_hover = dock_key[2]
        cached = self._ui_quad("dock", dock_key, lambda: vrui.dock(s.panels, selected_id, dock_hover), ui_meshes,
                               DOCK_DISTANCE)
        dock_model = _translate(0, DOCK_HEIGHT, -DOCK_DISTANCE) @ _tilt(DOCK_TILT)
        items.append(self._ui_item(cached, "dock", dock_model))
        # Music player: fixed just above the dock (makes room while a list / settings dialog is open)
        dialog_open = self.picker is not None or self.app_list_open or self.global_settings_open
        playing = self.media.now_playing
        player_hover = hover if hover and hover[0] == "media" else None
        player_key = (tuple(sorted(playing.items())) if playing else None, player_hover)
        cached = self._ui_quad("player", player_key, lambda: vrui.music_player(playing, player_hover), ui_meshes,
                               DOCK_DISTANCE)
        dock_h = self._ui_cache["dock"]["h"]
        if not dialog_open:
            items.append(self._ui_item(cached, "player",
                                       dock_model @ _translate(0, dock_h / 2 + 0.02 + cached["h"] / 2, 0)))
        # Settings for everything (dock's Settings button): like a dialog, at eye level in front of you
        if self.global_settings_open:
            dragging = self.pointer.active_slider()
            active_key = dragging[1] if dragging and dragging[0] is None else (
                hover[1][1] if hover and hover[0] == "slider" and hover[1][0] is None else None)
            toggle_hover = hover if hover and hover[0] == "toggle" else None
            values = tuple(getattr(s, key) for kind, key, *_ in vrui.GLOBAL_SETTINGS_ROWS if key)
            cached = self._ui_quad("settings", (values, active_key, toggle_hover),
                                   lambda: vrui.global_settings(s, active_key, toggle_hover), ui_meshes,
                                   PICKER_DISTANCE)
            items.append(self._ui_item(cached, "settings", _translate(0, 0.0, -PICKER_DISTANCE)))
        # Lists from the dock (Add window / Apps): like a dialog, at eye level in front of you
        list_hover = hover if hover and hover[0] in ("pick", "launch", "page") else None
        page = self.list_page
        if self.picker is not None:
            picker = self.picker
            cached = self._ui_quad("picker", (tuple(picker), list_hover, page),
                                   lambda: vrui.window_picker(picker, list_hover, page), ui_meshes, PICKER_DISTANCE)
            items.append(self._ui_item(cached, "picker", _translate(0, 0.0, -PICKER_DISTANCE)))
        elif self.app_list_open:
            entries = [(name, app_id) for name, app_id in self.apps]
            cached = self._ui_quad("apps", (len(entries), list_hover, page),
                                   lambda: vrui.app_list(entries, list_hover, page), ui_meshes, PICKER_DISTANCE)
            items.append(self._ui_item(cached, "apps", _translate(0, 0.0, -PICKER_DISTANCE)))
        return items

    def _look_center(self):
        """Where you are looking, as a window position around you (degrees, + = right) - new windows open
        there."""
        return -math.degrees(self.look_yaw)

    def _close_dialogs(self):
        """Close the dock's Settings card and lists (only one is open at a time)."""
        self.picker = None
        self.global_settings_open = False
        self.app_list_open = False
        self.list_page = 0

    def _on_shortcut(self, action):
        """Keyboard shortcuts (see settings.SHORTCUT_ACTIONS)."""
        if action == "recenter":
            self.recenter()
        elif action == "zoom":
            # The window under the pointer, or else the selected one
            hit = self.pointer.hit
            if hit and not hit[0].get("ui"):
                pid = hit[0]["id"]
            else:
                s = self.store.get()
                pid = s.panels[s.selected]["id"] if s.panels else None
            if pid:
                self.zoomed.symmetric_difference_update({pid})

    def _on_ui(self, action, payload):
        """Clicks on the VR interface (runs on the pointer's worker thread)."""
        panels = self.store.get().panels
        index = next((i for i, p in enumerate(panels) if p["id"] == payload), None) if isinstance(payload, str) \
            else None
        if action == "settings" and index is not None:
            self.settings_open.symmetric_difference_update({payload})
        elif action == "minimize" and index is not None:
            self.store.update_panel(index, shown=False)
            self.settings_open.discard(payload)
        elif action == "close" and index is not None:
            p = panels[index]
            hwnd = self.sources.hwnd_of(payload)
            if p["source"] == "window" and hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE: the app closes as usual
            else:
                self.store.remove_panel(index)
            self.settings_open.discard(payload)
        elif action == "tab" and index is not None:
            self.store.update_panel(index, shown=True)
            self.store.update(selected=index)
        elif action == "set" and payload[0] is None:   # a setting for everything
            self.store.update(**{payload[1]: payload[2]})
        elif action == "set":
            pid, key, value = payload
            index = next((i for i, p in enumerate(panels) if p["id"] == pid), None)
            if index is not None:
                if self.store.get().selected != index:
                    self.store.update(selected=index)
                self.store.update(**{key: value})
        elif action == "zoom" and index is not None:
            self.zoomed.symmetric_difference_update({payload})
        elif action in ("lock_position", "lock_look") and index is not None:
            self.store.update_panel(index, **{action: not panels[index].get(action, False)})
        elif action == "outside_click":
            # Clicking into a window or into space closes settings cards and lists.
            self.settings_open.clear()
            self._close_dialogs()
        elif action == "global_settings":
            opening = not self.global_settings_open
            self._close_dialogs()
            self.global_settings_open = opening
        elif action == "apps_menu":
            opening = not self.app_list_open
            self._close_dialogs()
            self.app_list_open = opening
        elif action == "page":
            self.list_page = max(0, self.list_page + payload)
        elif action == "launch":
            self._close_dialogs()
            self._launched_at = time.monotonic()   # its window comes into VR (see _adopt_new_windows)
            try:
                apps.launch(payload)
            except OSError:
                pass
        elif action == "toggle":
            self.store.update(**{payload: not getattr(self.store.get(), payload)})
        elif action == "add_menu":
            opening = self.picker is None
            self._close_dialogs()
            if opening:
                in_vr = {self.sources.hwnd_of(p["id"]) for p in panels}
                self._picker_windows = {hwnd: (title, exe) for hwnd, title, exe in windows.list_open_windows()
                                        if hwnd not in in_vr}
                in_use = {p["source"] for p in panels}
                views = [(f"Desktop view {k[4:]} (a whole desktop with big text)", k)
                         for k in displays.view_kinds() if k not in in_use]
                self.picker = ([(f"{title}   ({exe})", hwnd) for hwnd, (title, exe) in self._picker_windows.items()]
                               + views + [("Main monitor (whole screen - for videos, not for reading)", "main")])
        elif action == "pick":
            if payload == "main":
                self.store.add_panel(new_panel("main", "Main monitor"), self._look_center())
            elif isinstance(payload, str) and payload.startswith("view"):
                self.store.add_panel(new_panel(payload, f"Desktop view {payload[4:]}", width=3.0, distance=1.6,
                                               curvature=0.2), self._look_center())
            elif payload in self._picker_windows:
                title, exe = self._picker_windows[payload]
                self.store.add_panel(windows.new_window_panel(payload, title, exe), self._look_center())
            self.picker = None
        elif action == "media":
            self.media.command(payload)
        elif action == "recenter":
            self.recenter()
        elif action == "mouse_to_pc" and self.pointer.enabled:
            self.pointer.toggle()

    def _adopt_new_windows(self, s):
        """Once a second: an app window opened from VR (shortly after a click in VR) becomes a VR panel.
        Windows opened from the main screen stay on the main screen."""
        current = {hwnd: (title, exe) for hwnd, title, exe in windows.list_open_windows()}
        now = time.monotonic()
        launched = now - self._launched_at < LAUNCHED_FROM_VR_SECONDS   # Apps list in VR: always into VR
        clicked = s.auto_add_windows and self.pointer.active and now - self.pointer.last_click < OPENED_FROM_VR_SECONDS
        if self._known_windows is not None and (launched or clicked):
            in_vr = {p.get("hwnd") for p in s.panels}
            for hwnd, (title, exe) in current.items():
                if hwnd not in self._known_windows and hwnd not in in_vr:
                    self.store.add_panel(windows.new_window_panel(hwnd, title, exe), self._look_center())
        self._known_windows = set(current)

    def _lens(self, textures, ui_meshes):
        """Zoom lens: if the pointer is on a window with zoom switched on, a magnified view of the spot
        under the pointer, floating just in front of it. Returns (model, mesh, texture, center, span) or
        None."""
        hit = self.pointer.hit if self.pointer.active else None
        if not hit or hit[0].get("ui") or hit[0]["id"] not in self.zoomed:
            return None
        p, u, v, _ = hit
        entry = textures.get(p["id"])
        if not entry or not entry[1]:
            return None
        w, h = p["width"], p["height"]
        lw, lh = w * LENS_SIZE, w * LENS_SIZE * 0.6
        span = (lw / (w * LENS_ZOOM), lh / (h * LENS_ZOOM))
        center = (min(max(u, span[0] / 2), 1 - span[0] / 2), min(max(v, span[1] / 2), 1 - span[1] / 2))
        # Where the pointer is on the (maybe curved) window, in window space
        if p["curvature"] > 0.001:
            r = w / (2 * math.pi * p["curvature"])
            theta = (u - 0.5) * w / r
            x, z = r * math.sin(theta), r * (1 - math.cos(theta))
        else:
            theta, x, z = 0.0, (u - 0.5) * w, 0.0
        model = (p["model"] @ _translate(x, (0.5 - v) * h, z + 0.05) @ _rotate_y(-math.degrees(theta)))
        mkey = ("lens", round(lw, 3), round(lh, 3))
        if mkey not in ui_meshes:
            ui_meshes[mkey] = _mesh(*_screen_vertices(lw, lh, 0.0))
        fkey = ("lens-frame", round(lw, 3), round(lh, 3))
        if fkey not in ui_meshes:
            ui_meshes[fkey] = _mesh(*_screen_vertices(lw + 0.02, lh + 0.02, 0.0))
        return model, ui_meshes[mkey], ui_meshes[fkey], entry[0], center, span

    def _pointer_dot_models(self):
        """Pointer as a small disc facing you, where the pointer ray hits (or 2 m out)."""
        end = self.pointer.end_point()
        dist = float(np.linalg.norm(end)) or 1.0
        end = end * (1 - 0.01 / dist)   # a hair in front of the panel
        face = (_translate(*end) @ _rotate_y(math.degrees(self.pointer.yaw))
                @ _rotate_x(math.degrees(self.pointer.pitch)))
        size = 0.012 * dist
        return [(face @ _scale(size), (0.31, 0.61, 0.98, 1.0)),
                (face @ _translate(0, 0, 0.001) @ _scale(size * 0.55), (1.0, 1.0, 1.0, 1.0))]

    # -- rendering ---------------------------------------------------------------------------
    def _render_loop(self, window, sensors):
        scene = _program(SCENE_VS, SCREEN_FS)
        floor_prog = _program(SCENE_VS, FLOOR_FS)
        warp = _program(WARP_VS, WARP_FS)
        empty_vao = GL.glGenVertexArrays(1)

        rw, rh = int(EYE_W * RENDER_SCALE), int(EYE_H * RENDER_SCALE)
        eye_fbos, eye_texs = [], []
        for _ in range(2):
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, rw, rh, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            depth = GL.glGenRenderbuffers(1)
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, depth)
            GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, rw, rh)
            fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, depth)
            eye_fbos.append(fbo)
            eye_texs.append(tex)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

        floor_vao, _, floor_count = _mesh(
            np.array([(-10, FLOOR_Y, -10, 0, 0), (10, FLOOR_Y, -10, 1, 0),
                      (10, FLOOR_Y, 10, 1, 1), (-10, FLOOR_Y, 10, 0, 1)], np.float32),
            np.array([0, 1, 2, 0, 2, 3], np.uint32))

        color_prog = _program(SCENE_VS, COLOR_FS)
        lens_prog = _program(SCENE_VS, LENS_FS)
        dot_vao, _, dot_count = _mesh(*_disc_vertices())
        ui_meshes = {}     # (width, height) -> mesh for interface quads
        self._ui_cache = {}
        textures = {}      # panel id -> [gl texture, (w, h), seq]
        meshes = {}        # panel id -> (key, mesh); "frame:<id>" for the selection frame
        sources = {}
        sources_key = None
        sources_checked = 0.0

        self._recenter_event.set()
        frames, t_fps = 0, time.perf_counter()
        pointer_was_active = False

        while not self._stop_event.is_set() and not glfw.window_should_close(window):
            glfw.poll_events()
            s = self.store.get()

            if self._recenter_event.is_set():
                self._recenter_event.clear()
                self.tracker.recenter()
                self.pointer.recenter(0.0, 0.0)

            # Panels added/removed/changed, or a window closed / reappeared: update the picture sources.
            key = tuple((p["id"], p["source"], p.get("hwnd"), p["text_zoom"]) for p in s.panels)
            now = time.perf_counter()
            if key != sources_key or now - sources_checked > 1.0:
                sources = self.sources.sync(s.panels)
                sources_key, sources_checked = key, now
                # A window that was closed disappears from VR.
                for pid in self.sources.missing:
                    index = next((i for i, p in enumerate(self.store.get().panels) if p["id"] == pid), None)
                    if index is not None:
                        self.store.remove_panel(index)
                        self.settings_open.discard(pid)
                if self.sources.missing:
                    continue

            # New pictures?
            for pid, source in sources.items():
                seq, frame = source.latest()
                entry = textures.get(pid)
                if frame is None or (entry and entry[2] == seq):
                    continue
                if entry is None:
                    entry = textures[pid] = [_screen_texture(), None, -1]
                h, w = frame.shape[:2]
                self.aspects[pid] = h / w
                GL.glBindTexture(GL.GL_TEXTURE_2D, entry[0])
                if entry[1] != (w, h):
                    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, frame)
                    entry[1] = (w, h)
                else:
                    GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, frame)
                GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
                entry[2] = seq
            for pid in [pid for pid in textures if pid not in sources]:
                GL.glDeleteTextures(1, [textures.pop(pid)[0]])

            selected_id = s.panels[s.selected]["id"] if s.panels else None
            self.aspect = self.aspects.get(selected_id, self.aspect)
            draw_list = []   # (model matrix, mesh, texture or None, opacity, is_frame)
            pointer_panels = []
            for index, p in enumerate(s.panels):
                if not p.get("shown", True):   # minimized from the bar
                    continue
                pid = p["id"]
                aspect = self.aspects.get(pid, 9 / 16)
                mkey = (round(p["width"], 3), round(aspect, 4), round(p["curvature"], 3))
                if meshes.get(pid, (None,))[0] != mkey:
                    meshes[pid] = (mkey, _mesh(*_screen_vertices(p["width"], p["width"] * aspect, p["curvature"])))
                model = _panel_model(p)
                if pid == selected_id and len(s.panels) > 1:
                    fkey = (mkey, "frame")
                    if meshes.get("frame:" + pid, (None,))[0] != fkey:
                        meshes["frame:" + pid] = (fkey, _mesh(*_screen_vertices(
                            p["width"] + 0.06, p["width"] * aspect + 0.06, p["curvature"])))
                    draw_list.append((model @ _translate(0, 0, -0.01), meshes["frame:" + pid][1], None, 1.0, True))
                entry = textures.get(pid)
                draw_list.append((model, meshes[pid][1], entry[0] if entry and entry[1] else None, p["opacity"],
                                  False))
                source = sources.get(pid)
                pointer_panels.append({
                    "id": pid, "index": index, "source": p["source"], "model": model,
                    "hwnd": getattr(source, "hwnd", None), "width": p["width"], "height": p["width"] * aspect,
                    "curvature": p["curvature"], "yaw": p["yaw"], "distance": p["distance"],
                    "elevation": p["height"], "locked": p.get("lock_position", False)})

            # VR interface: bars on hovered windows, settings cards, the dock
            ui_items = self._build_ui(s, pointer_panels, selected_id, ui_meshes)
            pointer_panels = ui_items + pointer_panels

            # PSVR_STRAIGHT_VIEW=1: render as if looking straight ahead (layout checks with the headset off)
            q = (1.0, 0.0, 0.0, 0.0) if os.environ.get("PSVR_STRAIGHT_VIEW") else self.tracker.orientation()
            yaw, pitch = yaw_pitch(q)
            self.look_yaw = 0.0 if s.follow_head else math.atan2(math.sin(yaw), math.cos(yaw))
            self.look_pitch = 0.0 if s.follow_head else pitch
            head_rot = np.array(q_to_matrix(q), np.float32)

            tan_x = math.tan(math.radians(s.lens_fov / 2))
            proj = _perspective(tan_x, tan_x / (EYE_W / EYE_H))
            space = _rigid(head_rot, head_rot @ NECK - NECK) if s.follow_head else np.identity(4, np.float32)
            for pp in pointer_panels:
                pp["model"] = space @ pp["model"]
            self.pointer.set_panels(pointer_panels)
            self.headset_worn = sensors.proximity > WORN_PROXIMITY
            self.pointer.running = True
            if self.pointer.active and not pointer_was_active:
                self.pointer.recenter(yaw, pitch)   # mouse switched into VR: pointer starts where you look
                _keep_mouse_off_headset(True, s, self.headset_worn, pointer=True)
                self.pointer.park()
            elif pointer_was_active and not self.pointer.active:
                # Mouse back to the PC screen: put the cursor in the middle of the main monitor.
                _keep_mouse_off_headset(True, s, self.headset_worn, pointer=False)
                main = displays.find("main")
                if main:
                    ctypes.windll.user32.SetCursorPos(main.left + main.width // 2, main.top + main.height // 2)
            pointer_was_active = self.pointer.active
            if self.pointer.active:
                self.pointer.refresh()   # panels may have moved under a still pointer
            dot = self._pointer_dot_models() if self.pointer.active else None
            lens = self._lens(textures, ui_meshes)

            for eye, side in enumerate((-1.0, 1.0)):
                eye_offset = np.array([side * s.ipd / 2, 0.0, 0.0]) + NECK
                eye_pos = head_rot @ eye_offset - NECK
                view = np.linalg.inv(_rigid(head_rot, eye_pos))
                vp = proj @ view

                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, eye_fbos[eye])
                GL.glViewport(0, 0, rw, rh)
                GL.glClearColor(0.02, 0.025, 0.035, 1.0)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
                GL.glEnable(GL.GL_DEPTH_TEST)

                GL.glUseProgram(floor_prog)
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(floor_prog, "mvp"), 1, GL.GL_TRUE, vp)
                GL.glBindVertexArray(floor_vao)
                GL.glDrawElements(GL.GL_TRIANGLES, floor_count, GL.GL_UNSIGNED_INT, None)

                # Windows (hidden one by one with minimize, never all at once)
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                for model, mesh, tex, opacity, is_frame in draw_list:
                    mvp = vp @ space @ model
                    if tex is None:
                        # Selection frame, or a window that isn't available yet
                        GL.glUseProgram(color_prog)
                        GL.glUniformMatrix4fv(GL.glGetUniformLocation(color_prog, "mvp"), 1, GL.GL_TRUE, mvp)
                        color = (0.31, 0.61, 0.98, 1.0) if is_frame else (0.12, 0.14, 0.18, opacity)
                        GL.glUniform4f(GL.glGetUniformLocation(color_prog, "color"), *color)
                    else:
                        GL.glUseProgram(scene)
                        GL.glUniformMatrix4fv(GL.glGetUniformLocation(scene, "mvp"), 1, GL.GL_TRUE, mvp)
                        GL.glUniform1f(GL.glGetUniformLocation(scene, "opacity"), opacity)
                        GL.glActiveTexture(GL.GL_TEXTURE0)
                        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                    GL.glBindVertexArray(mesh[0])
                    GL.glDrawElements(GL.GL_TRIANGLES, mesh[2], GL.GL_UNSIGNED_INT, None)
                # VR interface on top of the windows
                GL.glUseProgram(scene)
                GL.glUniform1f(GL.glGetUniformLocation(scene, "opacity"), 1.0)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                for item in ui_items:
                    GL.glUniformMatrix4fv(GL.glGetUniformLocation(scene, "mvp"), 1, GL.GL_TRUE, vp @ item["model"])
                    GL.glBindTexture(GL.GL_TEXTURE_2D, item["texture"])
                    GL.glBindVertexArray(item["mesh"][0])
                    GL.glDrawElements(GL.GL_TRIANGLES, item["mesh"][2], GL.GL_UNSIGNED_INT, None)
                GL.glDisable(GL.GL_BLEND)

                if lens is not None:
                    model, mesh, frame_mesh, tex, center, span = lens
                    GL.glUseProgram(color_prog)
                    GL.glUniformMatrix4fv(GL.glGetUniformLocation(color_prog, "mvp"), 1, GL.GL_TRUE,
                                          vp @ model @ _translate(0, 0, -0.002))
                    GL.glUniform4f(GL.glGetUniformLocation(color_prog, "color"), 0.31, 0.61, 0.98, 1.0)
                    GL.glBindVertexArray(frame_mesh[0])
                    GL.glDrawElements(GL.GL_TRIANGLES, frame_mesh[2], GL.GL_UNSIGNED_INT, None)
                    GL.glUseProgram(lens_prog)
                    GL.glUniformMatrix4fv(GL.glGetUniformLocation(lens_prog, "mvp"), 1, GL.GL_TRUE, vp @ model)
                    GL.glUniform2f(GL.glGetUniformLocation(lens_prog, "center"), *center)
                    GL.glUniform2f(GL.glGetUniformLocation(lens_prog, "span"), *span)
                    GL.glActiveTexture(GL.GL_TEXTURE0)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                    GL.glBindVertexArray(mesh[0])
                    GL.glDrawElements(GL.GL_TRIANGLES, mesh[2], GL.GL_UNSIGNED_INT, None)

                if dot is not None:
                    # Pointer: blue ring + white center, always on top
                    GL.glDisable(GL.GL_DEPTH_TEST)
                    GL.glUseProgram(color_prog)
                    GL.glBindVertexArray(dot_vao)
                    for model, color in dot:
                        GL.glUniformMatrix4fv(GL.glGetUniformLocation(color_prog, "mvp"), 1, GL.GL_TRUE, vp @ model)
                        GL.glUniform4f(GL.glGetUniformLocation(color_prog, "color"), *color)
                        GL.glDrawElements(GL.GL_TRIANGLES, dot_count, GL.GL_UNSIGNED_INT, None)
                    GL.glEnable(GL.GL_DEPTH_TEST)

            # Lens correction into the two halves of the headset panel
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
            GL.glDisable(GL.GL_DEPTH_TEST)
            fb_w, fb_h = glfw.get_framebuffer_size(window)
            GL.glUseProgram(warp)
            k1, k2 = s.lens_k1, s.lens_k2
            GL.glUniform2f(GL.glGetUniformLocation(warp, "k"), k1, k2)
            GL.glUniform1f(GL.glGetUniformLocation(warp, "aspect"), EYE_W / EYE_H)
            GL.glUniform1f(GL.glGetUniformLocation(warp, "norm"), 1.0 + k1 + k2)
            GL.glUniform3f(GL.glGetUniformLocation(warp, "chroma_x"), *CHROMA["x"])
            GL.glUniform3f(GL.glGetUniformLocation(warp, "chroma_y"), *CHROMA["y"])
            GL.glBindVertexArray(empty_vao)
            for eye in range(2):
                GL.glViewport(eye * fb_w // 2, 0, fb_w // 2, fb_h)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, eye_texs[eye])
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            glfw.swap_buffers(window)

            frames += 1
            now = time.perf_counter()
            if now - t_fps >= 1.0:
                self.fps = frames / (now - t_fps)
                frames, t_fps = 0, now
                # Windows resets the clip at times
                _keep_mouse_off_headset(True, s, self.headset_worn, pointer=self.pointer.active)
                self._adopt_new_windows(s)
                if displays.ensure_headset_hz():
                    self.status = "Headset had dropped to 59 Hz - set back to 90 Hz"
