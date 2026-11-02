"""VR video around you: a sphere showing a 360° / 180° video, mono or 3D (one picture per eye).

The sphere is real: its radius is the video's distance (surface.model scales a unit sphere), so each eye sees
it from where that eye is - a sphere 1 m away looks 1 m away in every direction, like a window at 1 m.

The picture comes as YUV planes (video/player.py) and is turned into colors here. Every pixel looks up its
direction in the video's layout:
  equi     the usual 360° picture: left-right = all around, top-bottom = up to down
  equi180  the same for the half in front of you (VR180)
  mesh     YouTube's cube layout (equi-angular cubemap): 3 x 2 cube faces - top row left, front, right;
           bottom row down, back, up, turned on their side. Found by fitting a real YouTube frame.
  screen   a flat picture on a big screen in front of you (videos made for phone VR viewers)
3D videos hold one picture per eye, stacked (top = left eye) or side by side (left = left eye).

Viewer adjustments: size (the whole video bigger / smaller around you) and brightness.
"""
import ctypes

import numpy as np
from OpenGL import GL

from .gl import Mesh, Program


VS = """
#version 330 core
layout(location = 0) in vec3 pos;
uniform mat4 mvp;
out vec3 v_dir;
void main() { v_dir = pos; gl_Position = mvp * vec4(pos, 1.0); }
"""
FS = """
#version 330 core
in vec3 v_dir;
uniform sampler2D tex_y, tex_u, tex_v;
uniform int projection;   // 0 equi, 1 equi180, 2 mesh, 3 screen
uniform vec2 screen_half; // screen: half its width and height at distance 1
uniform int stereo;       // 0 mono, 1 top / bottom, 2 side by side
uniform int eye;          // 0 left, 1 right
uniform float opacity;
uniform float size;       // 180° / 360°: 1 = as filmed, 2 = twice as big
uniform float brightness;
out vec4 color;
const float PI = 3.14159265;

vec2 rot(vec2 st, int k) { for (int i = 0; i < k; i++) st = vec2(-st.y, st.x); return st; }

vec2 cube(vec3 d) {
    vec3 a = abs(d);
    vec2 st; vec2 slot; int k = 0;
    if (a.z >= a.x && a.z >= a.y) {
        if (d.z > 0.0) { st = vec2(d.x / d.z, -d.y / d.z); slot = vec2(1, 0); }          // front
        else { st = vec2(d.x / d.z, d.y / d.z); slot = vec2(1, 1); k = 1; }               // back
    } else if (a.x >= a.y) {
        if (d.x > 0.0) { st = vec2(-d.z / d.x, -d.y / d.x); slot = vec2(2, 0); }         // right
        else { st = vec2(-d.z / d.x, d.y / d.x); slot = vec2(0, 0); }                    // left
    } else {
        if (d.y > 0.0) { st = vec2(d.x / d.y, d.z / d.y); slot = vec2(2, 1); k = 3; }    // up
        else { st = vec2(-d.x / d.y, d.z / d.y); slot = vec2(0, 1); k = 3; }             // down
    }
    st = rot(atan(st) * (4.0 / PI), k);   // equi-angular: even detail across each face
    return (slot + (st + 1.0) * 0.5) / vec2(3.0, 2.0);
}

const vec4 BLACK = vec4(0.0, 0.0, 0.0, 1.0);

void main() {
    vec3 n = normalize(v_dir);
    vec3 d = vec3(n.x, n.y, -n.z);        // x right, y up, z = straight ahead
    vec2 uv;
    float lon = atan(d.x, d.z), lat = asin(clamp(d.y, -1.0, 1.0));
    if (projection != 3) {                // size: angles shrink as the video grows
        lon /= size; lat /= size;
        if (abs(lon) > PI || abs(lat) > PI * 0.5) { color = BLACK * opacity; return; }
        d = vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
    }
    if (projection == 3) {
        vec2 p = d.xy / max(d.z, 1e-4) / screen_half;
        if (d.z <= 0.0 || abs(p.x) > 1.0 || abs(p.y) > 1.0) { color = vec4(0.0, 0.0, 0.0, opacity); return; }
        uv = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5);
    } else if (projection == 2) {
        uv = cube(d);
    } else if (projection == 1) {
        if (abs(lon) > PI * 0.5) { color = vec4(0.0, 0.0, 0.0, opacity); return; }
        uv = vec2(lon / PI + 0.5, 0.5 - lat / PI);
    } else {
        uv = vec2(lon / (2.0 * PI) + 0.5, 0.5 - lat / PI);
    }
    if (stereo == 1) uv.y = uv.y * 0.5 + 0.5 * float(eye);
    else if (stereo == 2) uv.x = uv.x * 0.5 + 0.5 * float(eye);
    // BT.709, video range
    float y = (texture(tex_y, uv).r - 16.0 / 255.0) * (255.0 / 219.0);
    float cb = (texture(tex_u, uv).r - 0.5) * (255.0 / 224.0);
    float cr = (texture(tex_v, uv).r - 0.5) * (255.0 / 224.0);
    vec3 rgb = vec3(y + 1.5748 * cr, y - 0.1873 * cb - 0.4681 * cr, y + 1.8556 * cb);
    color = vec4(clamp(rgb * brightness, 0.0, 1.0), opacity);
}
"""
PROJECTIONS = {"equi": 0, "equi180": 1, "mesh": 2, "screen": 3}
STEREO = {"mono": 0, "top_bottom": 1, "side_by_side": 2}


def _sphere(rings=48, segments=96):
    verts, idx = [], []
    for i in range(rings + 1):
        lat = np.pi / 2 - np.pi * i / rings
        for j in range(segments + 1):
            lon = 2 * np.pi * j / segments
            verts.append((np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon), 0.0, 0.0))
    for i in range(rings):
        for j in range(segments):
            a, b = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            idx += [a, b, a + 1, a + 1, b, b + 1]
    return np.array(verts, np.float32), np.array(idx, np.uint32)


class VideoTexture:
    """The current video frame on the GPU: Y, U and V planes (one channel each). Frames go through pixel
    buffers: the copy to the GPU then runs in the background (a 4K frame takes ~2 ms of the frame instead of
    ~8 ms - at 90 fps a frame has 11 ms)."""

    def __init__(self):
        self.ids = GL.glGenTextures(3)
        self._buffers = GL.glGenBuffers(3)
        self.size = None

    @property
    def ready(self):
        return self.size is not None

    def upload(self, frame):
        """frame: video.player.Frame."""
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        for tex, buf, plane in zip(self.ids, self._buffers, (frame.y, frame.u, frame.v)):
            plane = np.ascontiguousarray(plane)
            h, w = plane.shape
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            if self.size != (frame.width, frame.height):
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, w, h, 0, GL.GL_RED, GL.GL_UNSIGNED_BYTE, None)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, buf)
            GL.glBufferData(GL.GL_PIXEL_UNPACK_BUFFER, plane.nbytes, None, GL.GL_STREAM_DRAW)   # a fresh one
            target = GL.glMapBufferRange(GL.GL_PIXEL_UNPACK_BUFFER, 0, plane.nbytes,
                                         GL.GL_MAP_WRITE_BIT | GL.GL_MAP_INVALIDATE_BUFFER_BIT)
            ctypes.memmove(target, plane.ctypes.data, plane.nbytes)
            GL.glUnmapBuffer(GL.GL_PIXEL_UNPACK_BUFFER)
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RED, GL.GL_UNSIGNED_BYTE, ctypes.c_void_p(0))
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
        self.size = (frame.width, frame.height)

    def bind(self):
        for unit, tex in enumerate(self.ids):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glActiveTexture(GL.GL_TEXTURE0)

    def delete(self):
        GL.glDeleteTextures(3, self.ids)
        GL.glDeleteBuffers(3, self._buffers)
        self.size = None


class VideoSphere:
    """Draws sphere surfaces (scene Surface with shape "sphere", texture = VideoTexture, model = placed and
    scaled unit sphere, data = {"projection", "stereo", "screen_degrees", "size", "brightness"})."""

    def __init__(self):
        self._prog = Program(VS, FS)
        prog = self._prog.use()
        for unit, name in enumerate(("tex_y", "tex_u", "tex_v")):
            prog.int(name, unit)
        self._mesh = Mesh(*_sphere())

    def draw(self, view_proj, surface, eye):
        if surface.texture is None or not surface.texture.ready:
            return
        prog = self._prog.use()
        prog.mat4("mvp", view_proj @ surface.model)
        prog.int("projection", PROJECTIONS.get(surface.data.get("projection"), 0))
        prog.int("stereo", STEREO.get(surface.data.get("stereo"), 0))
        data = surface.data
        prog.int("eye", eye)
        prog.float("size", data.get("size", 1.0))
        prog.float("brightness", data.get("brightness", 1.0))
        w, h = surface.texture.size
        stereo = surface.data.get("stereo")
        eye_aspect = (h / (w / 2)) if stereo == "side_by_side" else (h / 2 / w) if stereo == "top_bottom" else h / w
        half_w = float(np.tan(np.radians(surface.data.get("screen_degrees", 60.0) / 2)))
        prog.float("screen_half", half_w, half_w * eye_aspect)
        prog.float("opacity", surface.opacity)
        surface.texture.bind()
        GL.glDepthMask(GL.GL_FALSE)       # behind everything: never hides a window or the controls
        self._mesh.draw()
        GL.glDepthMask(GL.GL_TRUE)
