"""OpenGL building blocks: shader programs, meshes and textures. Only use these on the render thread."""
import ctypes

import numpy as np
from OpenGL import GL

GL_TEXTURE_MAX_ANISOTROPY = 0x84FE    # EXT_texture_filter_anisotropic (supported by all desktop GPUs)
GL_TEXTURE_LOD_BIAS = 0x8501
TEXT_SHARPEN_BIAS = -0.4              # pick slightly sharper mipmaps: scaled-down text stays crisper


class Program:
    def __init__(self, vertex_src, fragment_src):
        def stage(kind, src):
            s = GL.glCreateShader(kind)
            GL.glShaderSource(s, src)
            GL.glCompileShader(s)
            if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(s).decode())
            return s
        self.id = GL.glCreateProgram()
        GL.glAttachShader(self.id, stage(GL.GL_VERTEX_SHADER, vertex_src))
        GL.glAttachShader(self.id, stage(GL.GL_FRAGMENT_SHADER, fragment_src))
        GL.glLinkProgram(self.id)
        if not GL.glGetProgramiv(self.id, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(self.id).decode())
        self._locations = {}

    def use(self):
        GL.glUseProgram(self.id)
        return self

    def _loc(self, name):
        if name not in self._locations:
            self._locations[name] = GL.glGetUniformLocation(self.id, name)
        return self._locations[name]

    def mat4(self, name, m):
        GL.glUniformMatrix4fv(self._loc(name), 1, GL.GL_TRUE, m)

    def float(self, name, *values):
        {1: GL.glUniform1f, 2: GL.glUniform2f, 3: GL.glUniform3f, 4: GL.glUniform4f}[len(values)](
            self._loc(name), *values)


class Mesh:
    """Indexed triangles with position (xyz) and picture coordinates (uv)."""

    def __init__(self, vertices, indices):
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        self._buffers = GL.glGenBuffers(2)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._buffers[0])
        GL.glBufferData(GL.GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._buffers[1])
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 20, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, False, 20, ctypes.c_void_p(12))
        GL.glBindVertexArray(0)
        self.count = len(indices)

    def draw(self):
        GL.glBindVertexArray(self.vao)
        GL.glDrawElements(GL.GL_TRIANGLES, self.count, GL.GL_UNSIGNED_INT, None)

    def delete(self):
        GL.glDeleteBuffers(2, self._buffers)
        GL.glDeleteVertexArrays(1, [self.vao])


class MeshCache:
    """Meshes by shape key; meshes not used for a while are freed (sizes change while you drag a slider)."""

    def __init__(self, keep_frames=300):
        self._meshes = {}   # key -> [mesh, last frame used]
        self._frame = 0
        self._keep = keep_frames

    def get(self, key, make):
        entry = self._meshes.get(key)
        if entry is None:
            entry = self._meshes[key] = [Mesh(*make()), self._frame]
        entry[1] = self._frame
        return entry[0]

    def end_frame(self):
        self._frame += 1
        if self._frame % 120 == 0:
            for key in [k for k, (_, used) in self._meshes.items() if self._frame - used > self._keep]:
                self._meshes.pop(key)[0].delete()


class Texture:
    """A picture on the GPU, filtered for readable text: mipmaps against shimmering, anisotropic filtering so
    text on tilted / curved surfaces stays sharp, and a small bias toward the sharper mipmap level."""

    def __init__(self):
        self.id = GL.glGenTextures(1)
        self.size = None   # (width, height) in pixels once something was uploaded
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, 16.0)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_LOD_BIAS, TEXT_SHARPEN_BIAS)

    @property
    def ready(self):
        return self.size is not None

    @property
    def aspect(self):
        """Height / width of the picture."""
        return self.size[1] / self.size[0] if self.size else None

    def upload(self, pixels, bgra=False):
        """pixels: (h, w, 4) uint8 array (RGBA, or BGRA from screen capture) or a Pillow RGBA image."""
        if not isinstance(pixels, np.ndarray):
            pixels = np.asarray(pixels, np.uint8)
        h, w = pixels.shape[:2]
        fmt = GL.GL_BGRA if bgra else GL.GL_RGBA
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.id)
        if self.size != (w, h):
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, fmt, GL.GL_UNSIGNED_BYTE, pixels)
            self.size = (w, h)
        else:
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, fmt, GL.GL_UNSIGNED_BYTE, pixels)
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)

    def bind(self, unit=0):
        GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.id)

    def delete(self):
        GL.glDeleteTextures(1, [self.id])
        self.size = None
