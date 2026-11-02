"""DS4 motion: aim the VR pointer with the controller (a laser from your hand, or just the dot).

The DS4's gyro is read straight from the controller (HID, next to DS4Windows, which keeps doing the buttons
through XInput - see gamepad.py). Click the touchpad: the pointer comes into VR where you look, and the way the
controller points right then becomes "there" - point it where you look and click to line them up.
  Up / down is absolute: gravity tells how the controller is tilted, so the same tilt always means the same
  height and the pointer can't creep up or down.
  Left / right is relative (nothing tells the controller where "front" is): turning it turns the pointer;
  slow drift is ignored and the touchpad click lines it up again.

Measured on the user's DS4: gyro is right-handed in the same frame as the accelerometer, 16 units per
degree/second; front up = + around the controller's own x axis.
"""
import math
import struct
import threading
import time

import numpy as np

from ..core.log import log_error

SONY = 0x054C
CONTROLLERS = (0x05C4, 0x09CC, 0x0BA0)   # DS4 v1, DS4 v2, the USB wireless adapter
UNITS_PER_DEGREE = 16.0
# The gyro's rest value ("bias", a little different on every controller) is measured while the controller is
# really still: its readings barely change (lying on the desk; a hand always trembles more than this).
STILL_SPREAD = 4.0        # gyro units: readings this steady mean "still"
STILL_SECONDS = 0.6       # this long still: calibrated (first time) / the rest value is refreshed
BIAS_RATE = 0.01          # how fast a refreshed rest value is taken over
SMOOTH = 0.05             # running average / spread of the readings
# Left / right: slow drift (a hand turning without you noticing) is ignored, faster turns go through fully.
IGNORE_BELOW = 1.5        # degrees / second
FULL_FROM = 5.0           # degrees / second
# Up / down: the gyro follows quick tilts, gravity slowly corrects it (so it never drifts)
GRAVITY_PULL = 0.01
PITCH_STEP = 0.12         # degrees: smaller changes aren't sent (no jitter, no fight with the mouse)
SEND_SECONDS = 0.008
RETRY_SECONDS = 2.0
TOUCHPAD_CLICK = 0x02


def _find():
    import hid
    for pid in CONTROLLERS:
        for d in hid.enumerate(SONY, pid):
            return d["path"]
    return None


class DS4Motion(threading.Thread):
    def __init__(self, store, engine):
        super().__init__(daemon=True, name="ds4-motion")
        self.store, self.engine = store, engine
        self.status = "DS4 laser: no DS4 found (press its PS button)"
        self._stop_event = threading.Event()
        self._was_active = False
        self._reset()

    def _reset(self):
        self._bias = None                       # calibrated rest value, None until the first still moment
        self._mean = None                       # running average of the readings
        self._spread = 100.0                    # running spread of the readings
        self._still_since = None
        self._up = np.array([0.0, 1.0, 0.0])
        self._tilt = None                       # the controller's tilt (degrees, front up +)
        self._anchor = None                     # (controller tilt, pointer pitch in degrees) lined up together
        self._sent_pitch = None

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                path = _find()
                if path is None:
                    self.status = "DS4 laser: no DS4 found (press its PS button)"
                else:
                    self._read(path)
            except Exception:
                log_error("ds4 motion")
                self.status = "DS4 laser: lost the controller"
            self._stop_event.wait(RETRY_SECONDS)

    def _read(self, path):
        import hid
        device = hid.device()
        device.open_path(path)
        self._reset()
        self.status = "DS4 laser: calibrating - put the DS4 down or hold it still a moment"
        try:
            last, turned, sent, clicked = time.perf_counter(), 0.0, time.perf_counter(), False
            while not self._stop_event.is_set():
                report = device.read(100, 200)
                if not report:
                    continue
                if report[0] == 0x11 and len(report) >= 27:     # Bluetooth (full report)
                    data = bytes(report[3:])
                elif report[0] == 0x01 and len(report) >= 25:   # USB
                    data = bytes(report[1:])
                else:
                    continue
                now = time.perf_counter()
                dt, last = min(now - last, 0.05), now
                gyro = np.array(struct.unpack_from("<3h", data, 12), np.float64)
                accel = np.array(struct.unpack_from("<3h", data, 18), np.float64)
                turned += self._turn(gyro, accel, dt, now)
                click = bool(data[6] & TOUCHPAD_CLICK)
                if click and not clicked:
                    self._to_look()
                clicked = click
                if now - sent >= SEND_SECONDS:
                    self._send(turned)
                    turned, sent = 0.0, now
        finally:
            device.close()

    def _learn_rest(self, gyro, now):
        """Measure the rest value while the controller is really still."""
        if self._mean is None:
            self._mean = gyro.copy()
        self._spread += (np.abs(gyro - self._mean).max() - self._spread) * SMOOTH
        self._mean += (gyro - self._mean) * SMOOTH
        if self._spread > STILL_SPREAD:
            self._still_since = None
            return
        self._still_since = self._still_since or now
        if now - self._still_since < STILL_SECONDS:
            return
        if self._bias is None:
            self._bias = self._mean.copy()
            self.status = "DS4 laser: ready - point it where you look and click the touchpad"
        else:
            self._bias += (self._mean - self._bias) * BIAS_RATE

    def _turn(self, gyro, accel, dt, now):
        """Degrees the controller turned left (+) since the last report (0 until calibrated); keeps its tilt."""
        self._learn_rest(gyro, now)
        norm = np.linalg.norm(accel)
        if norm > 1000:
            self._up += (accel / norm - self._up) * 0.05      # gravity, smoothed: which way is up
            self._up /= np.linalg.norm(self._up)
        if self._bias is None:
            return 0.0
        rate = (gyro - self._bias) / UNITS_PER_DEGREE       # degrees / second
        up = self._up
        gravity_tilt = math.degrees(math.atan2(-up[2], math.hypot(up[0], up[1])))
        if self._tilt is None:
            self._tilt = gravity_tilt
        self._tilt += rate[0] * dt + (gravity_tilt - self._tilt) * GRAVITY_PULL
        speed = np.linalg.norm(rate)
        rate *= min(1.0, max(0.0, (speed - IGNORE_BELOW) / (FULL_FROM - IGNORE_BELOW)))
        return float(rate @ up) * dt

    def _send(self, turned):
        s = self.store.get()
        pointer = getattr(self.engine, "pointer", None)
        active = bool(s.gyro_pointer and pointer is not None and pointer.active)
        if active and not self._was_active and self._tilt is not None:
            self._anchor = (self._tilt, math.degrees(pointer.pitch))   # pointer came into VR: line up here
        self._was_active = active
        if not active or self._tilt is None:
            return
        if turned:
            pointer.turn(turned * s.gyro_speed, 0.0)
        if self._anchor is None:
            self._anchor = (self._tilt, math.degrees(pointer.pitch))
        tilt0, pitch0 = self._anchor
        pitch = pitch0 + (self._tilt - tilt0) * s.gyro_speed
        if self._sent_pitch is None or abs(pitch - self._sent_pitch) >= PITCH_STEP:
            pointer.aim_pitch(pitch)
            self._sent_pitch = pitch

    def _to_look(self):
        """Touchpad click: the pointer comes into VR (if it isn't) and goes where you look; the controller's tilt
        right now means "where you look" from now on."""
        pointer = getattr(self.engine, "pointer", None)
        if pointer is None or not self.store.get().gyro_pointer:
            return
        shell = getattr(self.engine, "shell", None)
        if getattr(self.engine, "handover", False) or (shell is not None and shell.game_in_front()):
            return                                 # the touchpad is the game's while you play
        if not pointer.enabled:
            pointer.enabled = True
        pointer.to_look()
        if self._tilt is not None:
            self._anchor = (self._tilt, math.degrees(pointer.look[1]))
            self._sent_pitch = None
        pointer.laser_seen = time.monotonic()    # show the laser right away
