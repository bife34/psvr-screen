"""PSVR Screen: your PC screen in VR, controlled from a 3D control panel and the gamepad."""
import ctypes

from .device.ds4_motion import DS4Motion
from .device.gamepad import GamepadMouse
from .panel.control_panel import ControlPanel
from .core.settings import SettingsStore
from .engine import DirectEngine
from .legacy.steamvr_overlay import VREngine
from .win import displays, windows


def main():
    # Real pixel coordinates on every monitor (the VR monitor uses its own scaling); must come before any window.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor aware v2
    displays.turn_on_virtual_monitors()   # they only exist while the app runs (see the end)
    store = SettingsStore.load()
    engine = DirectEngine(store) if store.get().engine == "direct" else VREngine(store)
    pad = GamepadMouse(store, engine)
    motion = DS4Motion(store, engine)
    pad.motion = motion                   # the control panel shows its status
    engine.start()
    pad.start()
    motion.start()
    try:
        ControlPanel(store, engine, pad).run()
    finally:
        pad.stop()
        motion.stop()
        engine.stop()
        engine.join(15)
        store.save()
        # No window may stay on an invisible monitor, now or when its app opens next time (apps reopen where
        # they were closed): bring them to the main monitor, then the invisible monitors go away. The headset
        # stays a normal monitor for Windows.
        windows.rescue_windows(("vr",) + tuple(displays.view_kinds()))
        displays.turn_off_virtual_monitors()


if __name__ == "__main__":
    main()
