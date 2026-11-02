"""The contract between the shell and the apps it shows in windows.

A window in the VR space (one entry of settings.panels) shows one Content. The shell gives it a place,
a title bar, a settings card and a dock tab; the content only provides its picture and handles the pointer
inside it. Everything here runs on the render thread.

`host` is the shell's side of the contract, see shell.shell.AppHost.
"""
from ..ui import theme
from ..ui.view import View


class Content:
    icon = theme.Icon.WINDOW

    def __init__(self, panel, host):
        self.window_id = panel["id"]
        self.host = host
        self.closed = False          # set when what it shows is gone: the shell then removes the window

    @property
    def texture(self):
        """render.gl.Texture with the current picture, or None (nothing to show yet)."""
        return None

    @property
    def aspect(self):
        """Height / width of the picture (the window takes this shape)."""
        tex = self.texture
        return tex.aspect if tex is not None and tex.ready else 9 / 16

    def update(self, panel, now):
        """Every frame. `panel`: this window's current settings (size, text size...)."""

    def on_pointer(self, e):
        """Pointer input inside the window (scene.events.PointerEvent)."""

    def request_close(self):
        """The window's close button: ask the app to close (it may ask to save first)."""
        self.closed = True

    def release(self):
        """The window left the VR space: free the picture, give back what it borrowed."""


class ViewContent(Content):
    """A native VR app: its page is a ui View (drawn sharp for the headset). The page has a fixed width in
    pixels and scales with the window, like everything else in a window."""
    PAGE_WIDTH_PX = 1600

    def __init__(self, panel, host, view):
        super().__init__(panel, host)
        self.view = view

    @property
    def texture(self):
        return self.view.texture

    @property
    def aspect(self):
        return self.view.size_px[1] / self.view.size_px[0]

    def update(self, panel, now):
        self.view.refresh()

    def on_pointer(self, e):
        self.view.on_pointer(e)

    def release(self):
        self.view.release()


class Page(View):
    """Base for a native app's page: a View that knows its window."""

    def __init__(self, host):
        super().__init__(m_per_px=1.0)   # the window scales the page; this is never used for its size
        self.host = host
