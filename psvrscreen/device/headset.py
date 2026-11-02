"""The headset as the rest of the app sees it: switch it on, where you look, is it on your head, recenter.

Headset drives the real PSVR (USB commands + sensors + head tracking). SimulatedHeadset needs no hardware:
you always look straight ahead - for working on the app with the headset off (see engine.py).
"""
import time

from .psvr import BUTTON_MIC_MUTE, PSVRControl, PSVRError, PSVRSensors
from .tracking import HeadTracker

WORN_PROXIMITY = 600     # proximity sensor: ~150 = nothing there, 1023 = headset on your head
CALIBRATE_SECONDS = 5


class Headset:
    def __init__(self, on_recenter_button):
        self._on_recenter_button = on_recenter_button
        self.tracker = HeadTracker()
        self._control = None
        self._sensors = None

    def start(self, stop_event, status):
        """Power on, read the sensors and wait until head tracking is calibrated (keep it still)."""
        self._control = PSVRControl()
        # Mic-mute button on the headset remote = recenter (doesn't touch Windows at all)
        self._sensors = PSVRSensors(self.tracker.add_sample,
                                    on_button=lambda bit: self._on_recenter_button() if bit == BUTTON_MIC_MUTE else None)
        self._sensors.start()
        self._control.power_on()
        time.sleep(0.2)
        status("Calibrating - keep the headset still for a moment...")
        deadline = time.monotonic() + CALIBRATE_SECONDS
        while not self.tracker.calibrated and time.monotonic() < deadline and not stop_event.is_set():
            time.sleep(0.05)
        if not self.tracker.calibrated:
            raise PSVRError("No head tracking data from the headset")

    def vr_mode(self):
        self._control.vr_mode()

    def orientation(self):
        """Head rotation as a quaternion (w, x, y, z)."""
        return self.tracker.orientation()

    def recenter(self):
        self.tracker.recenter()

    @property
    def worn(self):
        return self._sensors is not None and self._sensors.proximity > WORN_PROXIMITY

    def close(self):
        """Back to cinema mode (the normal floating screen) and let go of the USB device."""
        if self._control is not None:
            try:
                self._control.cinema_mode()
            finally:
                self._control.close()
        if self._sensors is not None:
            self._sensors.stop()


class SimulatedHeadset:
    def start(self, stop_event, status):
        pass

    def vr_mode(self):
        pass

    def orientation(self):
        return (1.0, 0.0, 0.0, 0.0)

    def recenter(self):
        pass

    worn = True

    def close(self):
        pass
