"""The headset's screen: a borderless window on its monitor, one image per eye, and the lens correction.

Each eye is rendered bigger than its half of the panel (the lens magnifies the center), then warped with
barrel distortion + chromatic correction into its half of the 1920x1080 panel.
"""
import math

import glfw
from OpenGL import GL
from PIL import Image

from ..core import geometry
from .gl import Program

EYE_W, EYE_H = 960, 1080               # each eye gets half of the 1920x1080 panel
RENDER_SCALE = 1.4                     # render bigger than the panel, the lens correction magnifies the center
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


class HeadsetDisplay:
    """simulator=True: a normal window on the main screen showing the same two-lens picture (no headset)."""

    def __init__(self, simulator=False):
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
        if not self.window:
            glfw.terminate()
            raise HeadsetError("could not open the headset window")
        glfw.make_context_current(self.window)
        glfw.swap_interval(1)
        glfw.set_input_mode(self.window, glfw.CURSOR, glfw.CURSOR_HIDDEN)

        self.eye_w, self.eye_h = int(EYE_W * RENDER_SCALE), int(EYE_H * RENDER_SCALE)
        self._fbos, self._textures = [], []
        for _ in range(2):
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, self.eye_w, self.eye_h, 0, GL.GL_RGBA,
                            GL.GL_UNSIGNED_BYTE, None)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            depth = GL.glGenRenderbuffers(1)
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, depth)
            GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, self.eye_w, self.eye_h)
            fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, depth)
            self._fbos.append(fbo)
            self._textures.append(tex)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        self._warp = Program(WARP_VS, WARP_FS)
        self._empty_vao = GL.glGenVertexArrays(1)

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

    def present(self, k1, k2):
        """Lens correction of both eye images into the two halves of the headset panel, then show it."""
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
        for eye in range(2):
            GL.glViewport(eye * fb_w // 2, 0, fb_w // 2, fb_h)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._textures[eye])
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
