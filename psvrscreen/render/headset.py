"""The headset's screen: a borderless window on its monitor, one image per eye, and the lens correction.

Each eye is rendered bigger than its half of the panel (the lens magnifies the center), then warped with
barrel distortion + chromatic correction into its half of the 1920x1080 panel.
"""
import ctypes
import math

import glfw
from OpenGL import GL
from PIL import Image

from ..core import geometry
from .gl import Program

EYE_W, EYE_H = 960, 1080               # each eye gets half of the 1920x1080 panel
RENDER_SCALE = 1.4                     # render bigger than the panel, the lens correction magnifies the center
QUALITY_SCALES = {"normal": 1.4, "high": 1.8, "max": 2.2}   # settings.picture_quality -> render scale
# Lens values from PSVRFramework (wiki: Optical characteristics); K1, K2 and FOV are in the settings.
CHROMA = {"x": (1.0, 1.0091, 1.0224), "y": (1.0, 1.0078, 1.0192)}

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
uniform vec2 center;     // where this eye's picture sits, moved from the middle of its half (your eye's place)
uniform int cross;       // 1: draw the alignment cross straight ahead
uniform vec3 cross_color;
out vec4 color;
vec2 src(vec2 q, float cx, float cy) { return vec2(q.x * cx, q.y * cy * aspect) * 0.5 + 0.5; }
float inside(vec2 uv) { return step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0); }
void main() {
    vec2 p = vec2(v_uv.x * 2.0 - 1.0, (v_uv.y * 2.0 - 1.0) / aspect) - center;
    float r2 = dot(p, p);
    vec2 q = p * (1.0 + k.x * r2 + k.y * r2 * r2) / norm;
    vec2 r = src(q, chroma_x.r, chroma_y.r);
    vec2 g = src(q, chroma_x.g, chroma_y.g);
    vec2 b = src(q, chroma_x.b, chroma_y.b);
    color = vec4(texture(eye, r).r * inside(r), texture(eye, g).g * inside(g), texture(eye, b).b * inside(b), 1.0);
    if (cross == 1) {   // straight ahead, far away: with both eyes the two crosses are one when the pictures line up
        vec2 c = abs(q);
        if ((c.x < 0.006 && c.y < 0.35) || (c.y < 0.006 && c.x < 0.35) || abs(length(q) - 0.2) < 0.004)
            color = vec4(cross_color, 1.0);
    }
}
"""


class HeadsetError(RuntimeError):
    pass


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


GWL_EXSTYLE, GWLP_HWNDPARENT = -20, -8   # extended style; for a top-level window: its owner
WS_EX_APPWINDOW = 0x40000                # "always on the taskbar" (GLFW sets it)
SW_HIDE, SW_SHOW, SW_SHOWNOACTIVATE = 0, 5, 4
DWMWA_EXCLUDED_FROM_PEEK = 12


def _hide_from_taskbar(hwnd):
    """The headset's window gets no taskbar button: clicking it there minimized it (the headset then showed the
    plain desktop). It's given a hidden owner window - owned windows aren't on the taskbar - and stays a normal
    window otherwise: a "tool window" style made Windows send every frame through its compositor, and the VR
    trembled. And it stays when Windows "peeks" (pointing at an app's preview on the taskbar hides every other
    window for a moment)."""
    flag = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_EXCLUDED_FROM_PEEK,
                                               ctypes.byref(flag), ctypes.sizeof(flag))
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = (ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    user32.SetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
    owner = user32.CreateWindowExW(0, "STATIC", "PSVR Screen - owner", 0, 0, 0, 0, 0, None, None, None, None)
    if not owner:
        return
    user32.ShowWindow(ctypes.c_void_p(hwnd), SW_HIDE)       # the taskbar only notices while it's hidden
    user32.SetWindowLongPtrW(ctypes.c_void_p(hwnd), GWLP_HWNDPARENT, ctypes.c_void_p(owner))
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    style = user32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
    user32.SetWindowLongPtrW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ctypes.c_void_p(style & ~WS_EX_APPWINDOW))
    user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOW)


class HeadsetDisplay:
    """simulator=True: a normal window on the main screen showing the same two-lens picture (no headset)."""

    def __init__(self, simulator=False):
        self.simulator = simulator
        if not glfw.init():
            raise HeadsetError("could not start OpenGL")
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.AUTO_ICONIFY, glfw.FALSE)
        glfw.window_hint(glfw.FOCUSED, glfw.FALSE)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
        if simulator:
            self.window = glfw.create_window(EYE_W * 2 * 2 // 3, EYE_H * 2 // 3, "PSVR Screen - simulator", None, None)
        else:
            monitor = _find_headset_monitor()
            if monitor is None:
                glfw.terminate()
                raise HeadsetError("Headset screen not found in Windows display settings")
            mode = glfw.get_video_mode(monitor)
            # Borderless window covering the headset's monitor (not exclusive fullscreen: that triggers the AMD
            # overlay popup and can change the display mode).
            glfw.window_hint(glfw.DECORATED, glfw.FALSE)
            glfw.window_hint(glfw.FLOATING, glfw.TRUE)
            self.window = glfw.create_window(mode.size.width, mode.size.height, "PSVR Screen - headset", None, None)
            if self.window:
                glfw.set_window_pos(self.window, *glfw.get_monitor_pos(monitor))
                _hide_from_taskbar(glfw.get_win32_window(self.window))
        if not self.window:
            glfw.terminate()
            raise HeadsetError("could not open the headset window")
        glfw.make_context_current(self.window)
        glfw.swap_interval(1)
        glfw.set_input_mode(self.window, glfw.CURSOR, glfw.CURSOR_HIDDEN)

        self.scale = None
        self._fbos, self._textures, self._depths = [], [], []
        self.set_scale(RENDER_SCALE)
        self._warp = Program(WARP_VS, WARP_FS)
        self._empty_vao = GL.glGenVertexArrays(1)

    def set_scale(self, scale):
        """Render each eye `scale` times the panel's pixels (more = smoother edges and text after the lens
        correction, more work for the graphics card). Cheap to call every frame."""
        if scale == self.scale:
            return
        if self._fbos:
            GL.glDeleteFramebuffers(2, self._fbos)
            GL.glDeleteTextures(2, self._textures)
            GL.glDeleteRenderbuffers(2, self._depths)
        self.scale = scale
        self.eye_w, self.eye_h = int(EYE_W * scale), int(EYE_H * scale)
        self._fbos, self._textures, self._depths = [], [], []
        for _ in range(2):
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, self.eye_w, self.eye_h, 0, GL.GL_RGBA,
                            GL.GL_UNSIGNED_BYTE, None)
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)      # the smaller levels exist from the start
            # Mipmaps: where the lens correction shrinks the picture (bigger scales) it stays smooth, not jagged
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            depth = GL.glGenRenderbuffers(1)
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, depth)
            GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, self.eye_w, self.eye_h)
            fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, depth)
            self._fbos.append(fbo)
            self._textures.append(tex)
            self._depths.append(depth)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

    def keep_on_top(self):
        """The headset's picture stays above every other window (an app brought to the front, a window that
        opened on the headset's monitor, would show flat in your eyes instead of the VR)."""
        if self.simulator:
            return
        hwnd = glfw.get_win32_window(self.window)
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # TOPMOST, no move/size/activate
        self.keep_shown()

    def keep_shown(self):
        """Never minimized (Win + D, "Show desktop", minimize all...): the headset would show the plain desktop,
        blurry through the lenses. Cheap: every frame."""
        if self.simulator:
            return
        hwnd = glfw.get_win32_window(self.window)
        if ctypes.windll.user32.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    @property
    def should_close(self):
        return glfw.window_should_close(self.window)

    @staticmethod
    def poll():
        glfw.poll_events()

    @staticmethod
    def projection(lens_fov):
        tan_x = math.tan(math.radians(lens_fov / 2))
        return geometry.perspective(tan_x, tan_x / (EYE_W / EYE_H))

    def begin_eye(self, eye):
        """Draw into eye 0 (left) or 1 (right)."""
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbos[eye])
        GL.glViewport(0, 0, self.eye_w, self.eye_h)

    def present(self, k1, k2, apart=0.0, height=0.0, cross=False):
        """Lens correction of both eye images into the two halves of the headset panel, then show it.
        apart / height: the eyes' pictures moved apart and one above the other, to line up with your eyes;
        cross: the alignment test (red cross for the left eye, green for the right)."""
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glDisable(GL.GL_DEPTH_TEST)
        fb_w, fb_h = glfw.get_framebuffer_size(self.window)
        warp = self._warp.use()
        warp.float("k", k1, k2)
        warp.float("aspect", EYE_W / EYE_H)
        warp.float("norm", 1.0 + k1 + k2)
        warp.float("chroma_x", *CHROMA["x"])
        warp.float("chroma_y", *CHROMA["y"])
        GL.glBindVertexArray(self._empty_vao)
        warp.int("cross", 1 if cross else 0)
        for eye in range(2):
            side = -1.0 if eye == 0 else 1.0
            warp.float("center", side * apart, -side * height / 2)
            warp.float("cross_color", *((1.0, 0.2, 0.2) if eye == 0 else (0.2, 1.0, 0.2)))
            GL.glViewport(eye * fb_w // 2, 0, fb_w // 2, fb_h)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._textures[eye])
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        glfw.swap_buffers(self.window)

    def snapshot(self, path):
        """Save what was just shown (both lenses) as an image file."""
        fb_w, fb_h = glfw.get_framebuffer_size(self.window)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glReadBuffer(GL.GL_FRONT)
        data = GL.glReadPixels(0, 0, fb_w, fb_h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        Image.frombytes("RGBA", (fb_w, fb_h), data).transpose(Image.FLIP_TOP_BOTTOM).save(path)

    def close(self):
        glfw.destroy_window(self.window)
        glfw.terminate()
