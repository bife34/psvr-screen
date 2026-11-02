"""Takes over the real mouse while it is "in VR", and the global keyboard shortcuts.

A low-level mouse hook sits in the path of every mouse movement on the PC, so it is only installed while the
mouse is in VR; otherwise the normal mouse is completely untouched. The hook itself must return at once:
Windows waits for it on every mouse event (and anything sent from inside it would pass through it again), so
it only hands the input to `on_input` - which must only queue it.

Input given to on_input: ("move", dx, dy), ("button", name, down, alt), ("wheel", delta, horizontal, alt).
"""
import ctypes
import threading
from ctypes import wintypes

from . import shortcuts

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0204, 0x0205, 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL, WM_HOTKEY, WM_QUIT = 0x020A, 0x020E, 0x0312, 0x0012
WM_RELOAD_SHORTCUTS = 0x8001   # WM_APP + 1
WM_UPDATE_HOOK = 0x8002        # WM_APP + 2: install / remove the mouse hook
LLMHF_INJECTED = 0x01
VK_MENU = 0x12
HOTKEY_ID = 0x5053
BUTTONS = {WM_LBUTTONDOWN: ("left", True), WM_LBUTTONUP: ("left", False),
           WM_RBUTTONDOWN: ("right", True), WM_RBUTTONUP: ("right", False),
           WM_MBUTTONDOWN: ("middle", True), WM_MBUTTONUP: ("middle", False)}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
kernel32.GetModuleHandleW.restype = wintypes.HMODULE   # default int would cut the 64-bit handle in half


class MouseHook:
    def __init__(self, on_input, on_shortcut, get_shortcuts):
        """on_input(event tuple) from the hook; on_shortcut(action) for a pressed shortcut;
        get_shortcuts() -> {action: "Ctrl+Alt+P"}."""
        self._on_input, self._on_shortcut, self._get_shortcuts = on_input, on_shortcut, get_shortcuts
        self._wanted = False
        self._hook = None
        self._thread_id = None
        self.ok = None                 # did Windows accept the hook (last time we asked)?
        self.shortcut_problems = []    # shortcuts that could not be registered
        self.physical_buttons = set()  # mouse buttons really held right now

    @property
    def installed(self):
        return self._hook is not None

    def want(self, wanted):
        """Install (True) or remove (False) the hook; happens on the hook's thread."""
        if wanted != self._wanted:
            self._wanted = wanted
            self._post(WM_UPDATE_HOOK)

    def reload_shortcuts(self):
        self._post(WM_RELOAD_SHORTCUTS)

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="mouse-hook").start()

    def stop(self):
        self._post(WM_QUIT)

    def _post(self, message):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, message, 0, 0)

    def _loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def proc(n_code, w_param, l_param):
            if n_code == 0 and self._wanted:
                info = MSLLHOOKSTRUCT.from_address(l_param)
                if not info.flags & LLMHF_INJECTED:   # our own injected clicks pass through
                    try:
                        self._take(w_param, info)
                    except Exception:
                        pass
                    return 1   # swallow: while the mouse is in VR the real cursor is ours
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def update_hook():
            if self._wanted and self._hook is None:
                self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, kernel32.GetModuleHandleW(None), 0) or None
                self.ok = self._hook is not None
                self.physical_buttons.clear()   # presses made while the mouse was on the PC screen don't count
            elif not self._wanted and self._hook is not None:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

        registered = self._register_shortcuts({})
        update_hook()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in registered:
                    self._on_shortcut(registered[msg.wParam])
                elif msg.message == WM_RELOAD_SHORTCUTS:
                    registered = self._register_shortcuts(registered)
                elif msg.message == WM_UPDATE_HOOK:
                    update_hook()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            if self._hook is not None:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    def _register_shortcuts(self, old):
        for hotkey_id in old:
            user32.UnregisterHotKey(None, hotkey_id)
        registered, self.shortcut_problems = {}, []
        for i, (action, keys) in enumerate(self._get_shortcuts().items()):
            parsed = shortcuts.parse(keys)
            if parsed and user32.RegisterHotKey(None, HOTKEY_ID + i, *parsed):
                registered[HOTKEY_ID + i] = action
            else:
                self.shortcut_problems.append(keys)   # invalid, or already used by another program
        return registered

    def _take(self, kind, info):
        """Inside the hook: only queue, never do real work here."""
        if kind == WM_MOUSEMOVE:
            cur = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cur))
            dx, dy = info.pt.x - cur.x, info.pt.y - cur.y
            if dx or dy:
                self._on_input(("move", dx, dy))
            return
        alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        if kind in BUTTONS:
            name, down = BUTTONS[kind]
            (self.physical_buttons.add if down else self.physical_buttons.discard)(name)
            self._on_input(("button", name, down, alt))
        elif kind in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            delta = ctypes.c_short(info.mouseData >> 16).value
            self._on_input(("wheel", delta, kind == WM_MOUSEHWHEEL, alt))
