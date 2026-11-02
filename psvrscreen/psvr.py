"""Direct PSVR access without SteamVR: commands over USB (interface 5) and head tracking over HID (interface 4).

Protocol from OpenHMD (src/drv_psvr) and PSVRFramework (wiki). SteamVR / iVRy must be closed:
Windows lets only one program use the command interface at a time.
"""
import struct
import threading
import time

import hid
import libusb_package
import usb.core
import usb.util

VENDOR_ID, PRODUCT_ID = 0x054C, 0x09AF
CONTROL_INTERFACE, SENSOR_INTERFACE = 5, 4
EP_COMMAND_OUT, EP_REPLY_IN = 0x04, 0x84

CMD_POWER = 0x17
CMD_VR_MODE = 0x23
CMD_DISPLAY_SETTINGS = 0x21

GYRO_SCALE = 0.00105           # rad/s per unit (OpenHMD)
ACCEL_SCALE = 9.81 / 16384     # m/s^2 per unit
BUTTON_VOLUME_UP, BUTTON_VOLUME_DOWN, BUTTON_MIC_MUTE = 0x02, 0x04, 0x08   # headset cable remote
CINEMA_SIZE_RANGE = (26, 80)   # small .. large
CINEMA_DISTANCE_RANGE = (20, 50)
BRIGHTNESS_RANGE = (0, 32)


class PSVRError(Exception):
    pass


def _report(command, payload, magic=0x00):
    return bytes([command, magic, 0xAA, len(payload)]) + bytes(payload)


class PSVRControl:
    """Sends commands to the headset's processor unit."""

    def __init__(self):
        backend = libusb_package.get_libusb1_backend()
        self._dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=backend)
        if self._dev is None:
            raise PSVRError("PSVR not found - is the processor unit on and the USB cable connected?")
        try:
            usb.util.claim_interface(self._dev, CONTROL_INTERFACE)
        except usb.core.USBError as e:
            if e.errno == 13:
                raise PSVRError("PSVR is in use by another program - close SteamVR first") from e
            raise

    def close(self):
        usb.util.release_interface(self._dev, CONTROL_INTERFACE)
        usb.util.dispose_resources(self._dev)

    def _send(self, data):
        self._dev.write(EP_COMMAND_OUT, data, timeout=1000)

    def read_reply(self, timeout_ms=200):
        """Next status/reply report from the headset, or None."""
        try:
            return bytes(self._dev.read(EP_REPLY_IN, 64, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return None

    def power_on(self):
        self._send(_report(CMD_POWER, [1, 0, 0, 0]))  # OpenHMD's 0x76 group id is rejected ("Bad Gp-ID")

    def vr_mode(self):
        """Full-panel mode: our app draws both eyes itself."""
        self._send(_report(CMD_VR_MODE, [1, 0, 0, 0]))

    def cinema_mode(self):
        """The processor unit shows the PC picture as a floating screen."""
        self._send(_report(CMD_VR_MODE, [0, 0, 0, 0]))

    def cinema_settings(self, size, distance, brightness=32):
        size = max(CINEMA_SIZE_RANGE[0], min(CINEMA_SIZE_RANGE[1], int(size)))
        distance = max(CINEMA_DISTANCE_RANGE[0], min(CINEMA_DISTANCE_RANGE[1], int(distance)))
        brightness = max(BRIGHTNESS_RANGE[0], min(BRIGHTNESS_RANGE[1], int(brightness)))
        payload = [0xC0, size, distance, 0x14, 0, 0, 0, 0, 0, 0, brightness, 0, 0, 0, 0, 0]
        self._send(_report(CMD_DISPLAY_SETTINGS, payload))


class SensorSample:
    __slots__ = ("tick", "gyro", "accel")

    def __init__(self, tick, gyro, accel):
        self.tick, self.gyro, self.accel = tick, gyro, accel


class PSVRSensors(threading.Thread):
    """Reads head-tracking packets (~1000/s, 2 samples each) and passes samples to a callback."""

    def __init__(self, on_sample, on_button=None):
        super().__init__(daemon=True, name="psvr-sensors")
        self._on_sample = on_sample
        self._on_button = on_button     # called with the button bit when a remote button is pressed
        self._stop_event = threading.Event()
        self.proximity = 0          # ~150 = nothing in front of the sensor, 1023 = headset worn
        self.packets = 0

    def stop(self):
        self._stop_event.set()

    def run(self):
        path = next((d["path"] for d in hid.enumerate(VENDOR_ID, PRODUCT_ID)
                     if d["interface_number"] == SENSOR_INTERFACE), None)
        if path is None:
            raise PSVRError("PSVR head tracking not found")
        dev = hid.device()
        dev.open_path(path)
        prev_buttons = 0
        try:
            while not self._stop_event.is_set():
                data = dev.read(64, 100)
                if len(data) < 64:
                    continue
                buf = bytes(data)
                self.packets += 1
                buttons = buf[0]
                pressed = buttons & ~prev_buttons
                prev_buttons = buttons
                if pressed and self._on_button:
                    for bit in (BUTTON_VOLUME_UP, BUTTON_VOLUME_DOWN, BUTTON_MIC_MUTE):
                        if pressed & bit:
                            self._on_button(bit)
                self.proximity = struct.unpack_from("<H", buf, 55)[0]
                for offset in (16, 32):
                    tick, gx, gy, gz, ax, ay, az = struct.unpack_from("<I3h3h", buf, offset)
                    # Axis mapping from OpenHMD: x <- [1], y <- [0], z <- -[2]
                    gyro = (gy * GYRO_SCALE, gx * GYRO_SCALE, -gz * GYRO_SCALE)
                    accel = (ay * ACCEL_SCALE, ax * ACCEL_SCALE, -az * ACCEL_SCALE)
                    self._on_sample(SensorSample(tick & 0xFFFFFF, gyro, accel))
        finally:
            dev.close()


if __name__ == "__main__":
    # Quick manual test:  python -m psvrscreen.psvr vr|cinema
    import sys

    ctl = PSVRControl()
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "cinema"
        ctl.power_on()
        time.sleep(0.3)
        ctl.vr_mode() if mode == "vr" else ctl.cinema_mode()
        print(f"sent: power on + {mode} mode")
        for _ in range(5):
            reply = ctl.read_reply()
            if reply:
                print("reply:", reply[:16].hex(" "))
    finally:
        ctl.close()
