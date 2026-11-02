"""Runs the VR: drives the PSVR without SteamVR and draws the shell for both eyes, every frame.

A session: switch the headset on and calibrate head tracking, prepare the monitors, open the headset's
screen, then per frame: head pose -> shell.update() -> pointer input -> render both eyes -> lens correction.
When the headset goes away the session ends (app windows go back to the main screen) and is retried.

Environment switches for working on the app:
  PSVR_SIMULATOR=1      no headset needed: the two-lens picture in a window on the main screen
  PSVR_STRAIGHT_VIEW=1  with the headset: render as if you look straight ahead (layout checks on the desk)
  PSVR_SNAPSHOT=<file>  save the two-lens picture to this image file every few seconds
"""
import ctypes
import math
import os
import threading
import time

import numpy as np

from .core import geometry
from .device.headset import Headset, SimulatedHeadset
from .device.psvr import PSVRError
from .device.tracking import q_to_matrix, yaw_pitch
from .render.headset import HeadsetDisplay, HeadsetError
from .render.renderer import Renderer
from .shell.shell import Shell
from .win import displays

NECK = np.array([0.0, 0.075, -0.08])   # eyes sit above and in front of the neck pivot
SNAPSHOT_SECONDS = 3.0


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _keep_mouse_off_headset(enable, mouse_in_vr=False):
    """The headset is a monitor too: keep the real mouse off it. Mouse on the main screen: it stays on the
    main monitor (not on the invisible VR monitor, where you'd lose it). Mouse in VR: the pointer places the
    real cursor on main + VR monitor itself."""
    if enable:
        rect = _RECT(*(displays.mouse_area("both") if mouse_in_vr else displays.mouse_area("shown", "main")))
        ctypes.windll.user32.ClipCursor(ctypes.byref(rect))
    else:
        ctypes.windll.user32.ClipCursor(None)


class DirectEngine(threading.Thread):
    def __init__(self, store):
        super().__init__(daemon=True, name="vr-engine")
        self.store = store
        self.simulator = bool(os.environ.get("PSVR_SIMULATOR"))
        self.straight_view = bool(os.environ.get("PSVR_STRAIGHT_VIEW"))
        self.snapshot_path = os.environ.get("PSVR_SNAPSHOT")
        self.shell = Shell(store, self.recenter)
        self.pointer = self.shell.pointer
        # Read by the control panel
        self.status = "Starting..."
        self.fps = 0.0
        self.look_yaw = 0.0          # radians, + = left
        self.look_pitch = 0.0
        self.headset_worn = False
        self._stop_event = threading.Event()
        self._recenter_event = threading.Event()

    @property
    def aspects(self):
        return self.shell.aspects

    def stop(self):
        self._stop_event.set()

    def recenter(self):
        """Make where you look "straight ahead" (any thread)."""
        self._recenter_event.set()

    def run(self):
        self.shell.start()
        try:
            while not self._stop_event.is_set():
                try:
                    self._session()
                except (PSVRError, HeadsetError) as e:
                    self.status = f"{e}. Retrying..."
                except Exception as e:
                    self.status = f"Error: {str(e).splitlines()[0][:150]}. Retrying..."
                self.fps = 0.0
                self._stop_event.wait(3)
        finally:
            self.shell.stop()

    def _session(self):
        headset = SimulatedHeadset() if self.simulator else Headset(self.recenter)
        display = None
        try:
            headset.start(self._stop_event, self._set_status)
            if not self.simulator:
                displays.ensure_headset_hz()
            displays.place_vr_monitor_left_of_main()
            displays.set_vr_monitor_mode(*displays.VR_MONITOR_MODE)   # many pixels, big scaling: round text
            displays.prepare_views()
            display = HeadsetDisplay(simulator=self.simulator)
            headset.vr_mode()
            _keep_mouse_off_headset(True)
            self.status = "Running - simulator (no headset)" if self.simulator else "Running - direct mode (no SteamVR)"
            self._frames(display, headset, Renderer())
        finally:
            self.pointer.running = False   # normal mouse back while the VR picture isn't running
            _keep_mouse_off_headset(False)
            if display is not None:
                self.shell.suspend()       # needs the GL context: before the display closes
                display.close()
            headset.close()

    def _set_status(self, text):
        self.status = text

    def _frames(self, display, headset, renderer):
        self._recenter_event.set()
        frames, t_fps, t_snapshot = 0, time.perf_counter(), time.perf_counter()
        was_active = False
        while not self._stop_event.is_set() and not display.should_close:
            display.poll()
            s = self.store.get()
            now = time.monotonic()
            if self._recenter_event.is_set():
                self._recenter_event.clear()
                headset.recenter()
                self.pointer.recenter(0.0, 0.0)
                self.shell.recentered()

            # Where you look
            q = (1.0, 0.0, 0.0, 0.0) if self.straight_view else headset.orientation()
            yaw, pitch = yaw_pitch(q)
            self.look_yaw = 0.0 if s.follow_head else math.atan2(math.sin(yaw), math.cos(yaw))
            self.look_pitch = 0.0 if s.follow_head else pitch
            self.shell.look_yaw = -math.degrees(self.look_yaw)
            head_rot = np.array(q_to_matrix(q), np.float32)
            self.headset_worn = headset.worn

            # The space and its input
            surfaces = self.shell.update(now)
            if s.follow_head:   # everything stuck to your view
                space = geometry.rigid(head_rot, head_rot @ NECK - NECK)
                for surface in surfaces:
                    surface.model = space @ surface.model
            self.pointer.running = True
            if self.pointer.active and not was_active:
                self.pointer.recenter(yaw, pitch)   # mouse switched into VR: the pointer starts where you look
                _keep_mouse_off_headset(True, mouse_in_vr=True)
                self.pointer.park()
            elif was_active and not self.pointer.active:
                _keep_mouse_off_headset(True)        # mouse back on the PC screen: in the middle of it
                main = displays.find("main")
                if main:
                    ctypes.windll.user32.SetCursorPos(main.left + main.width // 2, main.top + main.height // 2)
            was_active = self.pointer.active
            self.pointer.process(surfaces)
            surfaces = surfaces + self.shell.overlay()

            # Both eyes, then the lens correction
            proj = display.projection(s.lens_fov)
            for eye, side in enumerate((-1.0, 1.0)):
                eye_pos = head_rot @ (np.array([side * s.ipd / 2, 0.0, 0.0]) + NECK) - NECK
                view = np.linalg.inv(geometry.rigid(head_rot, eye_pos))
                display.begin_eye(eye)
                renderer.draw_eye(proj @ view, surfaces, eye)
            display.present(s.lens_k1, s.lens_k2)
            renderer.end_frame()

            frames += 1
            t = time.perf_counter()
            if t - t_fps >= 1.0:
                self.fps = frames / (t - t_fps)
                frames, t_fps = 0, t
                _keep_mouse_off_headset(True, mouse_in_vr=self.pointer.active)   # Windows resets it at times
                if not self.simulator and displays.ensure_headset_hz():
                    self.status = "Headset had dropped to 59 Hz - set back to 90 Hz"
            if self.snapshot_path and t - t_snapshot >= SNAPSHOT_SECONDS:
                t_snapshot = t
                display.snapshot(self.snapshot_path)
