"""Picture sources for the VR panels: a whole monitor, or a single app window (Windows Graphics Capture)."""
import threading

import numpy as np
from windows_capture import WindowsCapture

from .win import windows
from .legacy.steamvr_overlay import FrameGrabber


class WindowSource:
    """Captures one window. Windows only sends new frames when the window's content changes."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.closed = False
        self._seq = 0
        self._frame = None
        self._lock = threading.Lock()
        # No cursor in the picture: the VR pointer is drawn by the renderer (the real cursor waits off to the side).
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

        self._control = capture.start_free_threaded()

    def latest(self):
        with self._lock:
            return self._seq, self._frame

    def stop(self):
        self.closed = True
        try:
            self._control.stop()
        except Exception:
            pass


class MonitorSource:
    def __init__(self, store, kind):
        self._grabber = FrameGrabber(store, kind)
        self._grabber.start()
        self.closed = False

    def latest(self):
        return self._grabber.latest()

    def stop(self):
        self._grabber.stop()


class SourceManager:
    """Keeps one source per panel, moves window panels onto the VR monitor and back."""

    def __init__(self, store):
        self.store = store
        self.mover = windows.WindowMover()
        self._sources = {}   # panel id -> (source, key)
        self._attached = {}  # panel id -> hwnd it was showing (to notice when that window is closed)
        self.missing = []    # window panels whose window is gone (closed): remove them from VR
        self._placed = {}    # panel id -> (hwnd, text size) it was placed on the VR monitor with

    def hwnd_of(self, panel_id):
        return self._attached.get(panel_id)

    def sync(self, panels):
        """Call when panels change. Returns {panel id: source} for the panels that have a picture."""
        wanted = {}
        self.missing = []
        for slot, p in enumerate(panels):
            if p["source"] == "window":
                pid = p["id"]
                if pid in self._attached:
                    # Showing a window already: when that window closes, the panel goes too.
                    hwnd = self._attached[pid]
                    if not windows.is_alive(hwnd):
                        self.missing.append(pid)
                        del self._attached[pid]
                        continue
                else:
                    hwnd = p.get("hwnd")
                    if not (windows.is_alive(hwnd) and windows.exe_of(hwnd).lower() == p["exe"].lower()):
                        hwnd = windows.find_window(p["exe"], p["title"])   # saved from last time
                    if not hwnd:
                        self.missing.append(pid)
                        continue
                    self._attached[pid] = hwnd
                wanted[pid] = (("window", hwnd), p, slot)
            else:
                wanted[p["id"]] = (("monitor", p["source"]), p, slot)

        for pid in list(self._sources):
            source, key = self._sources[pid]
            if pid not in wanted or wanted[pid][0] != key or source.closed:
                source.stop()
                if key[0] == "window" and (pid not in wanted or wanted[pid][0] != key):
                    self.mover.back(key[1])
                del self._sources[pid]
        for pid in [pid for pid in self._attached if pid not in wanted and pid not in self.missing]:
            del self._attached[pid]   # panel removed from VR

        sharp = self.store.get().sharp_text
        for pid, (key, p, slot) in wanted.items():
            # Place a window on the VR monitor when it is added, or when the pixels it needs change
            # (text size, or - in sharp mode - its size / distance in VR).
            if key[0] == "window":
                size = windows.window_pixels(p, sharp)
                if self._placed.get(pid) != (key[1], size):
                    self.mover.to_vr_monitor(key[1], size, slot)
                    self._placed[pid] = (key[1], size)
                elif sharp and not p.get("lock_look"):
                    self._follow_window(pid, key[1], p, size)
            if pid not in self._sources:
                try:
                    source = WindowSource(key[1]) if key[0] == "window" else MonitorSource(self.store, key[1])
                except Exception:
                    continue
                self._sources[pid] = (source, key)
        return {pid: s for pid, (s, _) in self._sources.items()}

    def _follow_window(self, pid, hwnd, p, size):
        """The VR window follows the real one: when you resize or maximize the app yourself, or it refuses
        the size we asked for (Explorer's minimum height, Steam's minimum width...), the app keeps that size
        and the VR window grows/shrinks by the same factor and takes its shape - so the text stays the size
        it was and more (or less) fits inside - instead of forcing the app back."""
        actual_w, actual_h = windows.actual_size(hwnd)
        if abs(actual_w - size[0]) <= max(24, size[0] * 0.04) and abs(actual_h - size[1]) <= max(24, size[1] * 0.04):
            return
        index = next((i for i, x in enumerate(self.store.get().panels) if x["id"] == pid), None)
        if index is None:
            return
        k = actual_w / size[0]
        followed = dict(p, width=p["width"] * k, aspect=actual_h / max(1, actual_w),
                        pixels=windows.base_pixels(p) * k)
        self.store.update_panel(index, width=followed["width"], aspect=followed["aspect"], pixels=followed["pixels"])
        # Remember the app's own size as placed: no resize fight (the 20 px steps would differ a little).
        self._placed[pid] = (hwnd, windows.window_pixels(followed, True))

    def any_closed(self):
        return any(s.closed for s, _ in self._sources.values())

    def stop(self):
        for source, key in self._sources.values():
            source.stop()
        self._sources.clear()
        self.mover.back_all()
