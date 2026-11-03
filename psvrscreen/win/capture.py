"""Live pictures of an app window (Windows Graphics Capture) or of a whole monitor (screen grab).

Both run on their own thread and keep only the newest frame: `latest()` returns (sequence number, BGRA
numpy image or None); the number changes with every new frame.
"""
import ctypes
import threading
import time

import numpy as np
from windows_capture import WindowsCapture

from .screen_grab import Capture


class WindowCapture:
    """One app window. Windows only sends new frames when the window's content changes."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.closed = False          # the window was closed
        self.failed = False          # Windows wouldn't capture it (yet): see DesktopContent
        self._seq = 0
        self._frame = None
        self._lock = threading.Lock()
        # No cursor in the picture: the VR pointer is drawn by the renderer.
        # secondary_window: include the window's own dialogs and menus (Windows 11) - they'd be invisible otherwise
        capture = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd,
                                 minimum_update_interval=16, secondary_window=True)

        @capture.event
        def on_frame_arrived(frame, control):
            if self.closed:
                control.stop()
                return
            img = np.array(frame.frame_buffer, copy=True)   # the buffer is only valid during this call
            img[:, :, 3] = 255
            with self._lock:
                self._frame = img
                self._seq += 1

        @capture.event
        def on_closed():
            self.closed = True

        try:
            self._control = capture.start_free_threaded()
        except Exception as e:      # ("parameter incorrect": minimized / not shown yet / a protected window)
            self._control, self.failed = None, True
            from ..core.log import log_note
            log_note(f"[capture] window {hwnd} can't be captured now: {str(e)[:100]}")

    def latest(self):
        with self._lock:
            return self._seq, self._frame

    def stop(self):
        self.closed = True
        try:
            if self._control is not None:
                self._control.stop()
        except Exception:
            pass


def _desktop_layout():
    """Changes when monitors are added, removed or moved."""
    m = ctypes.windll.user32.GetSystemMetrics
    return m(76), m(77), m(78), m(79), m(80)   # virtual screen x, y, width, height, monitor count


class MonitorCapture(threading.Thread):
    """A whole monitor ("main", "vr", "view1"...), `fps()` times per second, with the cursor drawn in."""

    def __init__(self, kind, fps=lambda: 30):
        super().__init__(daemon=True, name=f"capture-{kind}")
        self.kind = kind
        self.closed = False
        self._fps = fps
        self._seq = 0
        self._frame = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.start()

    def latest(self):
        with self._lock:
            return self._seq, self._frame

    def stop(self):
        self._stop_event.set()

    def run(self):
        capture, layout = Capture(), _desktop_layout()   # mss: create and use on this thread
        try:
            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                if _desktop_layout() != layout:
                    layout = _desktop_layout()
                    capture.close()
                    capture = Capture()
                try:
                    frame = capture.grab(self.kind)
                except Exception:
                    capture.close()
                    capture = Capture()
                    self._stop_event.wait(0.5)
                    continue
                with self._lock:
                    self._frame = frame
                    self._seq += 1
                self._stop_event.wait(max(0.0, 1.0 / max(1, self._fps()) - (time.perf_counter() - t0)))
        finally:
            capture.close()
