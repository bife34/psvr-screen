"""Surroundings: a 360° picture all around you, far away, behind the windows (instead of the dark room).

The pictures are in psvr-screen/environments (CC0 scenes from Poly Haven, see CREDITS.txt there); any 360°
picture put there (twice as wide as tall) is a choice too, named by its file name. The middle of the picture is
in front of you; `turn` turns the scene around you, `brightness` dims it so windows stand out.
"""
import threading
from pathlib import Path

import numpy as np
from OpenGL import GL
from PIL import Image

from ..core import geometry
from ..core.paths import APP_DIR, DATA_DIR
from ..core.log import log_error
from .gl import Mesh, Program
from .video_sphere import _sphere

FOLDER = APP_DIR / "environments"
MINE = DATA_DIR / "my_surroundings"      # made from your photos ("Make it my room", video/room_maker.py)
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
RADIUS = 400.0          # far enough that both eyes see it as "at the horizon"
MAX_WIDTH = 8192
PREVIEW = (320, 160)

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
uniform sampler2D tex;
uniform float brightness;
uniform float turn;      // radians
out vec4 color;
const float PI = 3.14159265;
void main() {
    vec3 d = normalize(v_dir);
    float u = fract(0.5 + (atan(d.x, -d.z) + turn) / (2.0 * PI));
    float v = acos(clamp(d.y, -1.0, 1.0)) / PI;
    color = vec4(texture(tex, vec2(u, v)).rgb * brightness, 1.0);
}
"""


def names():
    """The surroundings to choose from (file names without extension), sorted."""
    found = set()
    for folder in (FOLDER, MINE):
        try:
            found.update(p.stem for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS)
        except OSError:
            pass
    return sorted(found)


def is_mine(name):
    """One made from your own picture (can be removed), not one of the app's scenes."""
    path = path_of(name)
    return path is not None and path.parent == MINE


def path_of(name):
    for folder in (MINE, FOLDER):
        found = next((p for p in folder.glob(glob_escape(name) + ".*") if p.suffix.lower() in EXTENSIONS), None)
        if found is not None:
            return found
    return None


def glob_escape(name):
    return "".join(f"[{c}]" if c in "[]*?" else c for c in name)


def forget_preview(name):
    _previews.pop(name, None)


_previews = {}


def preview(name):
    """A small picture of what's in front of you in this scene (made once, kept)."""
    if name not in _previews:
        try:
            with Image.open(path_of(name)) as image:
                image.draft("RGB", (1024, 512))
                image = image.convert("RGB")
            w, h = image.size
            front = image.crop((w // 4, h // 4, w * 3 // 4, h * 3 // 4))   # the front half, around the horizon
            _previews[name] = front.resize(PREVIEW, Image.LANCZOS)
        except Exception:
            _previews[name] = None
    return _previews[name]


def warm_previews():
    """Make the previews in the background (Settings shows them without a stall)."""
    threading.Thread(target=lambda: [preview(n) for n in names()], daemon=True, name="env-previews").start()


class Environment:
    """Draws the chosen surroundings (render thread). The picture is read in the background."""

    def __init__(self):
        self._prog = Program(VS, FS)
        self._mesh = Mesh(*_sphere(rings=64, segments=128))
        self._texture = None
        self._name = None             # the chosen one
        self._loaded = None           # (name, RGB array) read by the background thread, to upload
        self._shown = None            # name whose picture is on the GPU

    def choose(self, name):
        """name: a scene from names(), or "" / None for none. Cheap to call every frame."""
        name = name or None
        if name == self._name:
            return
        self._name = name
        if name is not None:
            threading.Thread(target=self._read, args=(name,), daemon=True, name="environment").start()

    def _read(self, name):
        try:
            with Image.open(path_of(name)) as image:
                image.draft("RGB", (MAX_WIDTH, MAX_WIDTH // 2))
                image = image.convert("RGB")
                if image.width > MAX_WIDTH:
                    image = image.resize((MAX_WIDTH, MAX_WIDTH // 2), Image.LANCZOS)
                pixels = np.ascontiguousarray(np.asarray(image))
            if name == self._name:
                self._loaded = (name, pixels)
        except Exception:
            log_error(f"environment {name}")

    @property
    def active(self):
        """Something is shown (the room's floor isn't needed)."""
        return self._name is not None and self._shown == self._name

    def draw(self, view_proj, brightness=1.0, turn_degrees=0.0):
        if self._loaded is not None:
            name, pixels = self._loaded
            self._loaded = None
            self._upload(pixels)
            self._shown = name
        if not self.active:
            return
        prog = self._prog.use()
        prog.mat4("mvp", view_proj @ geometry.scale(RADIUS))
        prog.float("brightness", brightness)
        prog.float("turn", np.radians(turn_degrees))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
        GL.glDepthMask(GL.GL_FALSE)       # always behind everything
        GL.glDisable(GL.GL_CULL_FACE)
        self._mesh.draw()
        GL.glDepthMask(GL.GL_TRUE)

    def _upload(self, pixels):
        if self._texture is None:
            self._texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
        # No mipmaps: they'd draw a line where the picture's left and right edges meet. 4K over 360° is
        # about the headset's own sharpness, so plain linear filtering is enough.
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        h, w = pixels.shape[:2]
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB8, w, h, 0, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, pixels)

    def release(self):
        if self._texture is not None:
            GL.glDeleteTextures(1, [self._texture])
        self._texture, self._shown, self._name = None, None, None
