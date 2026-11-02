"""PSVR Screen: your PC screen in VR, controlled from a 3D control panel and the gamepad."""
import ctypes

from .device.gamepad import GamepadMouse
from .panel.control_panel import ControlPanel
from .core.settings import SettingsStore
from .engine import DirectEngine
from .legacy.steamvr_overlay import VREngine
from .win import displays, windows


def main():
    # Real pixel coordinates on every monitor (the VR monitor uses its own scaling); must come before any window.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor aware v2
    store = SettingsStore.load()
    engine = DirectEngine(store) if store.get().engine == "direct" else VREngine(store)
    pad = GamepadMouse(store, engine)
    engine.start()
    pad.start()
    try:
        ControlPanel(store, engine, pad).run()
    finally:
        pad.stop()
        engine.stop()
        engine.join(15)
        store.save()
        # Last safety net (the VR side may not have finished in time): no window stays on an invisible monitor.
        windows.rescue_windows(("vr",) + tuple(displays.view_kinds()))


if __name__ == "__main__":
    main()
