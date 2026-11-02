"""DS4 motion: aim the VR pointer by turning the controller (a laser from your hand).

The DS4's gyro is read straight from the controller (HID, next to DS4Windows, which keeps doing the buttons
through XInput - see gamepad.py). Click the touchpad: the laser comes on (the pointer goes into VR) where you
look; click it again later to bring the pointer back to where you look. Turning the controller turns the
pointer like a mouse does: relative, so it never drifts away.

Measured on the user's DS4 v2: gyro is right-handed in the same frame as the accelerometer, 16 units per
degree/second. Left / right = turning around "up" (whatever way you hold it: gravity says where up is);
up / down = tilting around the controller's left-right axis.
"""
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
# Slow drift (a hand sinking without you noticing) is ignored, faster turns go through fully.
IGNORE_BELOW = 1.5        # degrees / second
FULL_FROM = 5.0           # degrees / second
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
        self._reset()

    def _reset(self):
        self._bias = None                       # calibrated rest value, None until the first still moment
        self._mean = None                       # running average of the readings
        self._spread = 100.0                    # running spread of the readings
        self._still_since = None
        self._up = np.array([0.0, 1.0, 0.0])

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
            last, pending, sent, clicked = time.perf_counter(), [0.0, 0.0], time.perf_counter(), False
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
                click = bool(data[6] & TOUCHPAD_CLICK)
                if click and not clicked:
                    self._to_look()
                clicked = click
                yaw, pitch = self._turn(gyro, accel, dt, now)
                pending[0] += yaw
                pending[1] += pitch
                if now - sent >= SEND_SECONDS and (pending[0] or pending[1]):
                    self._send(*pending)
                    pending, sent = [0.0, 0.0], now
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
            self.status = "DS4 laser: ready - click the touchpad to aim"
        else:
            self._bias += (self._mean - self._bias) * BIAS_RATE

    def _turn(self, gyro, accel, dt, now):
        """Degrees the controller turned left (+) and up (+) since the last report (0 until calibrated)."""
        self._learn_rest(gyro, now)
        norm = np.linalg.norm(accel)
        if norm > 1000:
            self._up += (accel / norm - self._up) * 0.05      # gravity, smoothed: which way is up
            self._up /= np.linalg.norm(self._up)
        if self._bias is None:
            return 0.0, 0.0
        rate = (gyro - self._bias) / UNITS_PER_DEGREE       # degrees / second
        speed = np.linalg.norm(rate)
        rate *= min(1.0, max(0.0, (speed - IGNORE_BELOW) / (FULL_FROM - IGNORE_BELOW)))
        return float(rate @ self._up) * dt, float(rate[0]) * dt

    def _send(self, yaw, pitch):
        s = self.store.get()
        pointer = getattr(self.engine, "pointer", None)
        if not s.gyro_pointer or pointer is None or not pointer.active:
            return
        pointer.turn(yaw * s.gyro_speed, pitch * s.gyro_speed)

    def _to_look(self):
        """Touchpad click: the pointer goes into VR if it isn't (the engine puts it where you look), else it
        jumps to where you look."""
        pointer = getattr(self.engine, "pointer", None)
        if pointer is None or not self.store.get().gyro_pointer:
            return
        if not pointer.enabled:
            pointer.enabled = True
        pointer.to_look()
        pointer.laser_seen = time.monotonic()    # show the laser right away
