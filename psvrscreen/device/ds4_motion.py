"""DS4 motion: aim the VR pointer by turning the controller (a laser from your hand).

The DS4's gyro is read straight from the controller (HID, next to DS4Windows, which keeps doing the buttons
through XInput - see gamepad.py). Turning the controller turns the pointer like a mouse does: relative, so it
never drifts away; click the touchpad to put the pointer where you look.

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
STILL_UNITS = 45          # gyro this close to its rest value: the controller is lying / held still
BIAS_RATE = 0.02          # how fast the rest value is learned while still
DEADZONE = 1.2            # degrees / second ignored (hand tremor)
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
        self.status = "DS4 motion: no controller"
        self._stop_event = threading.Event()
        self._bias = None
        self._up = np.array([0.0, 1.0, 0.0])

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                path = _find()
                if path is None:
                    self.status = "DS4 motion: no controller"
                else:
                    self._read(path)
            except Exception:
                log_error("ds4 motion")
                self.status = "DS4 motion: lost the controller"
            self._stop_event.wait(RETRY_SECONDS)

    def _read(self, path):
        import hid
        device = hid.device()
        device.open_path(path)
        self.status = "DS4 motion: ready"
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
                yaw, pitch = self._turn(gyro, accel, dt)
                pending[0] += yaw
                pending[1] += pitch
                if now - sent >= SEND_SECONDS and (pending[0] or pending[1]):
                    self._send(*pending)
                    pending, sent = [0.0, 0.0], now
        finally:
            device.close()

    def _turn(self, gyro, accel, dt):
        """Degrees the controller turned left (+) and up (+) since the last report."""
        if self._bias is None:
            self._bias = gyro.copy()
        if np.abs(gyro - self._bias).max() < STILL_UNITS:
            self._bias += (gyro - self._bias) * BIAS_RATE
        norm = np.linalg.norm(accel)
        if norm > 1000:
            self._up += (accel / norm - self._up) * 0.05      # gravity, smoothed: which way is up
            self._up /= np.linalg.norm(self._up)
        rate = (gyro - self._bias) / UNITS_PER_DEGREE       # degrees / second
        if np.linalg.norm(rate) < DEADZONE:
            return 0.0, 0.0
        return float(rate @ self._up) * dt, float(rate[0]) * dt

    def _send(self, yaw, pitch):
        s = self.store.get()
        pointer = getattr(self.engine, "pointer", None)
        if not s.gyro_pointer or pointer is None or not pointer.active:
            return
        pointer.turn(yaw * s.gyro_speed, pitch * s.gyro_speed)

    def _to_look(self):
        pointer = getattr(self.engine, "pointer", None)
        if pointer is not None and pointer.active and self.store.get().gyro_pointer:
            pointer.to_look()
