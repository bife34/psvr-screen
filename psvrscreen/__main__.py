"""PSVR Screen: your PC screen in VR, controlled from a 3D control panel and the gamepad."""
import ctypes
import faulthandler
import os
import subprocess
import sys
import threading
import time

from .device.ds4_motion import DS4Motion
from .device.gamepad import GamepadMouse
from .panel.control_panel import ControlPanel
from .core.log import ERROR_LOG, log_error
from .core.settings import SettingsStore
from .engine import DirectEngine
from .legacy.steamvr_overlay import VREngine
from .win import displays, windows

ENGINE_SECONDS = 8        # quitting: the VR picture stops within this
STEP_SECONDS = 10         # ...each cleanup step (moving windows back, monitors off) at most this
WATCHDOG_SECONDS = 40     # ...and the whole app is gone after this, whatever hangs (it's written to the log)


def _step(name, action, seconds=STEP_SECONDS):
    """Run one cleanup step without letting it hang the shutdown: a step that doesn't finish in time is left
    behind (the process ends anyway)."""
    def run():
        try:
            action()
        except Exception:
            log_error(f"quit: {name}")
    t = threading.Thread(target=run, daemon=True, name=f"quit-{name}")
    t.start()
    t.join(seconds)
    if t.is_alive():
        _note(f"quit: '{name}' took longer than {seconds} s - left behind")


def _note(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def _restart():
    """A new PSVR Screen, started once this one is gone."""
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        args = [sys.executable, "-m", "psvrscreen"]
    subprocess.Popen(args, cwd=os.getcwd(), creationflags=subprocess.DETACHED_PROCESS)


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
    then = None
    try:
        then = ControlPanel(store, engine, pad).run()
    except Exception:
        log_error("control panel")
    finally:
        # Quitting must always finish: if something hangs, the log gets where every thread was.
        log = open(ERROR_LOG, "a", encoding="utf-8")
        faulthandler.dump_traceback_later(WATCHDOG_SECONDS - 5, file=log)
        threading.Timer(WATCHDOG_SECONDS, lambda: (_note("quit: watchdog ended the app"), os._exit(1))).start()
        pad.stop()
        motion.stop()
        engine.stop()
        engine.join(ENGINE_SECONDS)
        if engine.is_alive():
            _note(f"quit: the VR picture didn't stop within {ENGINE_SECONDS} s")
        _step("save settings", store.save)
        # No window may stay on an invisible monitor, now or when its app opens next time (apps reopen where
        # they were closed): bring them to the main monitor, then the invisible monitors go away. The headset
        # stays a normal monitor for Windows.
        _step("windows back", lambda: windows.rescue_windows(("vr",) + tuple(displays.view_kinds())))
        _step("monitors off", displays.turn_off_virtual_monitors)
        faulthandler.cancel_dump_traceback_later()
        if then == "restart":
            _step("restart", _restart)
        # Done: end now. Libraries' own exit handlers (sound, AI) can wait forever on threads of theirs.
        log.close()
        os._exit(0)


if __name__ == "__main__":
    main()
