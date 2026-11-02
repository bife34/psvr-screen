"""VR video around you: a sphere showing a 360° / 180° video, mono or 3D (one picture per eye).

The sphere is real: its radius is the video's distance, so each eye sees it from where that eye is - a sphere
1 m away looks 1 m away in every direction, like a window at 1 m.

The picture comes as YUV planes (video/player.py) and is turned into colors here. Every pixel looks up its
direction in the video's layout:
  equi     the usual 360° picture: left-right = all around, top-bottom = up to down
  equi180  the same for the half in front of you (VR180)
  mesh     YouTube's cube layout (equi-angular cubemap): 3 x 2 cube faces - top row left, front, right;
           bottom row down, back, up, turned on their side. Found by fitting a real YouTube frame.
  fisheye  round pictures from fisheye lenses (190° - 220°): the angle from straight ahead grows evenly
           from the middle of the circle to its edge (equidistant)
  screen   a screen in front of you (videos made for phone VR viewers): a real panel at the distance, as
           wide as `screen_degrees` - farther away it is bigger, so it always looks the same size. Flat, or
           bent like a window (curve 1 = around you: every point as far away)
3D videos hold one picture per eye, stacked (top = left eye) or side by side (left = left eye).
The picture is a video frame (YUV planes) or a browser window's picture (BGRA, maybe with black borders
around the video: `crop` is the part that holds it).

Viewer adjustments: size (the whole video bigger / smaller around you), eye separation (IPD: each eye's
picture turned a little apart - the main control for how far a 3D video feels; + = farther), brightness /
contrast / saturation, and "move away": the
video goes away from you like a real object and gets smaller. You stay in the middle: a 180° / 360° dome
grows around you in every direction (like a cinema whose screens all move back) while its picture keeps
its real size on it; a screen moves straight back. "place": where you are inside the dome (parts of its
radius right / up / forward) - the dome moves the other way around you.
"""
import ctypes

import numpy as np
from OpenGL import GL

from ..core import geometry
from .gl import Mesh, Program


VS = """
#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
uniform mat4 mvp;
out vec3 v_dir;
out vec2 v_uv;
void main() { v_dir = pos; v_uv = uv; gl_Position = mvp * vec4(pos, 1.0); }
"""
FS = """
#version 330 core
in vec3 v_dir;
in vec2 v_uv;             // screen: the place on the screen's picture
uniform sampler2D tex_y, tex_u, tex_v;
uniform int projection;   // 0 equi, 1 equi180, 2 mesh, 3 screen, 4 fisheye
uniform float fisheye_fov; // radians the fisheye lens sees
uniform int rgb;          // 1: tex_y holds the colors (a window's picture), 0: Y / U / V planes
uniform vec4 crop;        // the part of the picture that holds the video: x0, y0, x1, y1
uniform int stereo;       // 0 mono, 1 top / bottom, 2 side by side
uniform int eye;          // 0 left, 1 right
uniform float opacity;
uniform float size;       // 180° / 360°: 1 = as filmed, 2 = twice as big
uniform float brightness;
uniform float contrast;
uniform float saturation;
uniform float separation; // radians the eyes' pictures are turned apart (+ = farther)
out vec4 color;
const float PI = 3.14159265;

vec3 adjust(vec3 c) {
    c = (c - 0.5) * contrast + 0.5;
    c = mix(vec3(dot(c, vec3(0.2126, 0.7152, 0.0722))), c, saturation);
    return clamp(c * brightness, 0.0, 1.0);
}

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
    float turn = (eye == 0 ? 0.5 : -0.5) * separation;
    d = vec3(d.x * cos(turn) + d.z * sin(turn), d.y, -d.x * sin(turn) + d.z * cos(turn));
    vec2 uv;
    float lon = atan(d.x, d.z), lat = asin(clamp(d.y, -1.0, 1.0));
    if (projection != 3) {                // size: angles shrink as the video grows
        lon /= size; lat /= size;
        if (abs(lon) > PI || abs(lat) > PI * 0.5) { color = BLACK * opacity; return; }
        d = vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
    }
    if (projection == 3) {
        uv = v_uv;
    } else if (projection == 4) {
        float theta = acos(clamp(d.z, -1.0, 1.0));
        if (theta > fisheye_fov * 0.5) { color = BLACK * opacity; return; }
        float r = theta / fisheye_fov;            // 0 in the middle, 0.5 at the circle's edge
        float phi = atan(d.y, d.x);
        uv = vec2(0.5 + r * cos(phi), 0.5 - r * sin(phi));
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
    uv = mix(crop.xy, crop.zw, uv);
    if (rgb == 1) { color = vec4(adjust(texture(tex_y, uv).rgb), opacity); return; }
    // BT.709, video range
    float y = (texture(tex_y, uv).r - 16.0 / 255.0) * (255.0 / 219.0);
    float cb = (texture(tex_u, uv).r - 0.5) * (255.0 / 224.0);
    float cr = (texture(tex_v, uv).r - 0.5) * (255.0 / 224.0);
    vec3 rgb = vec3(y + 1.5748 * cr, y - 0.1873 * cb - 0.4681 * cr, y + 1.8556 * cb);
    color = vec4(adjust(rgb), opacity);
}
"""
PROJECTIONS = {"equi": 0, "equi180": 1, "mesh": 2, "screen": 3, "fisheye": 4}
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


def _screen_shape(distance, degrees, curve):
    """(width along the surface, curvature) of a screen `distance` away whose edges are `degrees` apart as
    seen from your eyes - however much it is bent. curve 0 = flat; 1 = a circle around you (radius = distance);
    in between the circle's center lies farther away, behind you never."""
    half = np.radians(degrees / 2)
    if curve < 0.001:
        return 2 * distance * np.tan(half), 0.0
    radius = distance / curve                  # circle through the screen's middle; its center is in front
    # edge at angle phi on the circle seen at angle `half`: R sin(phi) = tan(half) (D - R + R cos(phi))
    phi = half + np.arcsin(np.clip(np.sin(half) * (distance - radius) / radius, -1.0, 1.0))
    width = 2 * radius * phi
    return width, width / (2 * np.pi * radius)


class VideoTexture:
    """The current video frame on the GPU: Y, U and V planes (one channel each). Frames go through pixel
    buffers: the copy to the GPU then runs in the background (a 4K frame takes ~2 ms of the frame instead of
    ~8 ms - at 90 fps a frame has 11 ms)."""

    def __init__(self):
        self.ids = GL.glGenTextures(3)
        self._buffers = GL.glGenBuffers(3)
        self.size = None
        self.rgb = False          # holding a window's picture (colors in the first texture)

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
        self.size, self.rgb = (frame.width, frame.height), False

    def upload_bgra(self, image):
        """A window's picture: (h, w, 4) BGRA uint8."""
        image = np.ascontiguousarray(image)
        h, w = image.shape[:2]
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.ids[0])
        if self.size != (w, h) or not self.rgb:
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, None)
            for name, value in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                                (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
                GL.glTexParameteri(GL.GL_TEXTURE_2D, name, value)
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, self._buffers[0])
        GL.glBufferData(GL.GL_PIXEL_UNPACK_BUFFER, image.nbytes, None, GL.GL_STREAM_DRAW)
        target = GL.glMapBufferRange(GL.GL_PIXEL_UNPACK_BUFFER, 0, image.nbytes,
                                     GL.GL_MAP_WRITE_BIT | GL.GL_MAP_INVALIDATE_BUFFER_BIT)
        ctypes.memmove(target, image.ctypes.data, image.nbytes)
        GL.glUnmapBuffer(GL.GL_PIXEL_UNPACK_BUFFER)
        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, ctypes.c_void_p(0))
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)
        self.size, self.rgb = (w, h), True

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
    """Draws sphere surfaces (scene Surface with shape "sphere", texture = VideoTexture, model = where the
    video's front is, data = {"projection", "stereo", "distance", "screen_degrees", "screen_curve", "size",
    "brightness", "contrast", "saturation", "ipd" (degrees), "move_away", "place" (right, up, forward), "fov"
    (fisheye, degrees), "crop"}). Screen videos
    are drawn on a panel instead of the sphere."""

    def __init__(self):
        self._prog = Program(VS, FS)
        prog = self._prog.use()
        for unit, name in enumerate(("tex_y", "tex_u", "tex_v")):
            prog.int(name, unit)
        self._mesh = Mesh(*_sphere())
        self._panel, self._panel_shape = None, None

    def draw(self, view_proj, surface, eye):
        if surface.texture is None or not surface.texture.ready:
            return
        prog = self._prog.use()
        projection = surface.data.get("projection")
        prog.int("projection", PROJECTIONS.get(projection, 0))
        prog.int("stereo", STEREO.get(surface.data.get("stereo"), 0))
        data = surface.data
        prog.int("eye", eye)
        prog.int("rgb", 1 if surface.texture.rgb else 0)
        prog.float("crop", *data.get("crop", (0.0, 0.0, 1.0, 1.0)))
        prog.float("fisheye_fov", float(np.radians(data.get("fov", 190.0))))
        prog.float("brightness", data.get("brightness", 1.0))
        prog.float("contrast", data.get("contrast", 1.0))
        prog.float("saturation", data.get("saturation", 1.0))
        prog.float("separation", float(np.radians(data.get("ipd", 0.0))))
        x0, y0, x1, y1 = data.get("crop", (0.0, 0.0, 1.0, 1.0))
        w, h = surface.texture.size[0] * (x1 - x0), surface.texture.size[1] * (y1 - y0)
        stereo = surface.data.get("stereo")
        eye_aspect = (h / (w / 2)) if stereo == "side_by_side" else (h / 2 / w) if stereo == "top_bottom" else h / w
        distance = data.get("distance", 10.0)
        away = data.get("move_away", 0.0)
        right, up, forward = data.get("place", (0.0, 0.0, 0.0))
        r = distance + away   # you move inside the dome = the dome moves the other way (front is -z)
        placed = surface.model @ geometry.translate(-right * r, -up * r, forward * r)
        if projection == "screen":
            width, curvature = _screen_shape(distance, data.get("screen_degrees", 60.0), data.get("screen_curve", 0.0))
            flat_width = 2 * distance * np.tan(np.radians(data.get("screen_degrees", 60.0) / 2))
            mesh = self._panel_mesh(width, flat_width * eye_aspect, curvature)
            model = placed @ geometry.translate(0, 0, -distance - away)
            prog.float("size", 1.0)
        else:   # the dome grows; the picture keeps its real size on it, so it covers less of your view
            mesh, model = self._mesh, placed @ geometry.scale(distance + away)
            prog.float("size", data.get("size", 1.0) * distance / (distance + away))
        prog.mat4("mvp", view_proj @ model.astype(np.float32))
        prog.float("opacity", surface.opacity)
        surface.texture.bind()
        GL.glDepthMask(GL.GL_FALSE)       # behind everything: never hides a window or the controls
        mesh.draw()
        GL.glDepthMask(GL.GL_TRUE)

    def _panel_mesh(self, width, height, curvature):
        shape = (round(width, 3), round(height, 3), round(curvature, 4))
        if shape != self._panel_shape:
            if self._panel is not None:
                self._panel.delete()
            self._panel, self._panel_shape = Mesh(*geometry.surface_vertices(width, height, curvature)), shape
        return self._panel
