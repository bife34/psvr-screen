"""The AI depth of what's shown around you (ai/depth.py), worked out in the background: once for a photo, over
and over for a playing video (each new depth blends with the last one, so it doesn't flicker).

The render thread calls `photo(...)` / `video_frame(...)` and takes `result()` (a depth map, or None) to upload."""
import threading

import numpy as np

from ..ai import depth as ai_depth
from ..ai import models
from ..core.log import log_error

PHOTO_INPUT = 1024             # a photo is looked at this big (the model works at ~518 anyway)
VIDEO_INPUT = 720
BLEND = 0.55                   # a video's new depth counts this much (the rest: the last one)


class DepthJob:
    def __init__(self):
        self._ready = None
        self._busy = False
        self._photo_key = None
        self._last = None
        self.state = ""            # "", "downloading", "working", "failed"

    def result(self):
        r, self._ready = self._ready, None
        return r

    def photo(self, path, bgra, private):
        """A photo's depth (once per photo)."""
        if self._photo_key == path or self._busy:
            return
        self._photo_key, self._busy, self.state = path, True, "working"

        def work():
            try:
                if not self._models():
                    return
                import cv2
                h, w = bgra.shape[:2]
                k = min(1.0, PHOTO_INPUT / max(h, w))
                rgb = cv2.cvtColor(cv2.resize(bgra, (max(1, round(w * k)), max(1, round(h * k))),
                                              interpolation=cv2.INTER_AREA), cv2.COLOR_BGRA2RGB)
                self._ready = ai_depth.of_picture(path, rgb, private)
                self.state = ""
            except Exception:
                log_error("photo depth")
                self.state = "failed"
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True, name="photo-depth").start()

    def video_frame(self, frame):
        """A video frame (video.player.Frame: Y, U, V planes) - taken only when the last one is done."""
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                if not self._models():
                    return
                import cv2
                step = max(1, frame.width // VIDEO_INPUT)
                y = frame.y[::step, ::step]
                u = frame.u[::step, ::step]
                v = frame.v[::step, ::step]
                h, w = (y.shape[0] // 2) * 2, (y.shape[1] // 2) * 2
                y, u, v = y[:h, :w], u[:h // 2, :w // 2], v[:h // 2, :w // 2]
                i420 = np.concatenate([y.ravel(), u.ravel(), v.ravel()]).reshape(h * 3 // 2, w)
                rgb = cv2.cvtColor(np.ascontiguousarray(i420), cv2.COLOR_YUV2RGB_I420)
                d = ai_depth.estimate(rgb, ai_depth.VIDEO_SIDE)
                if self._last is not None and self._last.shape == d.shape:
                    d = BLEND * d + (1 - BLEND) * self._last
                self._last = d
                self._ready = d
                self.state = ""
            except Exception:
                log_error("video depth")
                self.state = "failed"
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True, name="video-depth").start()

    def _models(self):
        if not models.ready("depth"):
            self.state = "downloading"
            if not models.ensure("depth"):
                self.state = "failed"
                return False
        return True

    def reset(self):
        self._photo_key, self._last, self._ready = None, None, None
