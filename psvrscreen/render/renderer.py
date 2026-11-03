"""Draws one eye's view: the room (environment) or a VR video around you, and the surfaces, in layer order."""
import numpy as np
from OpenGL import GL

from ..core import geometry
from ..scene.surface import BLACK, INTERFACE, OVERLAY, WORLD
from .gl import MeshCache, Mesh, Program
from .environment import Environment
from .video_sphere import VideoSphere

FLOOR_Y = -1.2                         # floor below your eyes (seated), meters
BACKGROUND = (0.02, 0.025, 0.035, 1.0)

SCENE_VS = """
#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
uniform mat4 mvp;
uniform vec4 uv_rect;    // part of the texture: u0, v0, u1, v1
out vec2 v_uv;
out vec3 v_pos;
void main() { v_uv = mix(uv_rect.xy, uv_rect.zw, uv); v_pos = pos; gl_Position = mvp * vec4(pos, 1.0); }
"""
TEXTURE_FS = """
#version 330 core
in vec2 v_uv;
uniform sampler2D tex;
uniform float opacity;
uniform float smoothing;   // + = smoother (less shimmer / pixels on small text), - = sharper
out vec4 color;
void main() { vec4 c = texture(tex, v_uv, smoothing); color = vec4(c.rgb, c.a * opacity); }
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


class Renderer:
    def __init__(self):
        self._textured = Program(SCENE_VS, TEXTURE_FS)
        self._color = Program(SCENE_VS, COLOR_FS)
        self._floor_prog = Program(SCENE_VS, FLOOR_FS)
        self._floor = Mesh(np.array([(-10, FLOOR_Y, -10, 0, 0), (10, FLOOR_Y, -10, 1, 0),
                                     (10, FLOOR_Y, 10, 1, 1), (-10, FLOOR_Y, 10, 0, 1)], np.float32),
                           np.array([0, 1, 2, 0, 2, 3], np.uint32))
        self._disc = Mesh(*geometry.disc_vertices())
        self.meshes = MeshCache()
        self._video = VideoSphere()
        self.environment = Environment()
        self._environment_look = (1.0, 0.0)   # brightness, turn
        self.smoothing = 0.0                  # settings.picture_smoothing
        self.black = False                    # plain black around you (no floor)


    def set_environment(self, name, brightness, turn):
        """The surroundings to show (every frame; cheap when nothing changed). BLACK: plain black, no floor."""
        self.black = name == BLACK
        self.environment.choose(None if self.black else name)
        self._environment_look = (brightness, turn)

    def _mesh(self, s):
        if s.shape == "disc":
            return self._disc
        key = (round(s.width, 3), round(s.height, 3), round(s.curvature, 3))
        return self.meshes.get(key, lambda: geometry.surface_vertices(s.width, s.height, s.curvature))

    def draw_eye(self, view_proj, surfaces, eye=0):
        """eye: 0 left, 1 right (3D videos show each eye its own picture)."""
        GL.glClearColor(*BACKGROUND)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_DEPTH_TEST)
        spheres = [s for s in surfaces if s.shape == "sphere"]
        if spheres:                     # a VR video replaces the room
            for s in spheres:
                self._video.draw(view_proj, s, eye)
            surfaces = [s for s in surfaces if s.shape != "sphere"]
        else:
            self.environment.draw(view_proj, *self._environment_look)
        if not spheres and not self.environment.active and not self.black:
            floor = self._floor_prog.use()
            floor.mat4("mvp", view_proj)
            floor.float("uv_rect", 0.0, 0.0, 1.0, 1.0)
            self._floor.draw()

        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        for layer in (WORLD, INTERFACE, OVERLAY):
            if layer == OVERLAY:
                GL.glDisable(GL.GL_DEPTH_TEST)
            for s in surfaces:
                if s.layer == layer:
                    self._draw(view_proj, s)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)

    def _draw(self, view_proj, s):
        mvp = view_proj @ s.model
        if s.texture is not None and s.texture.ready:
            prog = self._textured.use()
            prog.float("opacity", s.opacity)
            prog.float("smoothing", self.smoothing)
            s.texture.bind()
        else:
            prog = self._color.use()
            r, g, b, a = s.color
            prog.float("color", r, g, b, a * s.opacity)
        prog.mat4("mvp", mvp)
        prog.float("uv_rect", *s.uv)
        self._mesh(s).draw()

    def end_frame(self):
        self.meshes.end_frame()
