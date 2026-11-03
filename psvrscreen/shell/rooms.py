"""Rooms: separate spaces, each with its own windows and surroundings ("Work", "Cinema"...), switched from the
dock. And locked rooms: kept encrypted (core/vault.py), opened with a PIN on a keypad in VR - a hidden one isn't
listed anywhere: tap the dock's clock three times, type its PIN, and it opens. Leaving it (another room, Lock,
turning the app off) locks it again; nothing of it is ever written to settings.json.

Render thread, except the slow key work (scrypt, ~0.5 s a try) which runs in the background.
"""
import threading
import time
import uuid

from ..core import vault
from ..core.log import log_error
from ..ui import theme
from ..ui.theme import Icon
from ..ui.view import View
from ..ui.widgets import Button, Label, Panel, Row, Spacer
from .dialogs import DIALOG_DISTANCE

ROOM_KEYS = ("panels", "selected", "environment", "environment_brightness", "environment_turn", "layout")
ROOM_NAMES = ("Work", "Cinema", "Photos", "Games", "Relax", "Studio")
SECRET_TAPS, SECRET_SECONDS = 3, 1.5      # taps on the dock's clock that open the keypad
FAILS_BEFORE_WAIT, WAIT_SECONDS = 5, 30   # wrong codes in a row, then the keypad waits
PIN_MIN, PIN_MAX = 4, 12


class Rooms:
    def __init__(self, shell):
        self.shell = shell
        self.secret = None            # (room id, room key, name) while you're in a locked room
        self._extra = {}              # ...and what else it keeps (its safe's password)
        self.safe = None              # its safe (core.cryptomator.Vault), opened when you go in
        self.busy = False             # trying a code / making a locked room
        self._taps = []
        self._fails, self._wait_until = 0, 0.0
        s = shell.store.get()
        if not s.rooms:               # the first time: what's around you now is the room "Home"
            shell.store.update(rooms=[{"id": "home", "name": "Main room", **self._snapshot(s)}], current_room="home")
        elif s.current_room not in {r["id"] for r in s.rooms}:
            self._load(next(r for r in s.rooms))   # (closed while in a locked room: back to a normal one)
        shell.store.before_save = self._before_save

    # -- what a room holds ------------------------------------------------------------------------------
    @staticmethod
    def _snapshot(s):
        return {k: getattr(s, k) for k in ROOM_KEYS}

    def _load(self, room, room_id=None):
        values = {k: room[k] for k in ROOM_KEYS if k in room}
        values.setdefault("panels", [])
        values["selected"] = min(values.get("selected", 0), max(0, len(values["panels"]) - 1))
        self.shell.store.update(current_room=room_id or room["id"], **values)

    def current_name(self):
        if self.secret:
            return self.secret[2]
        s = self.shell.settings()
        return next((r["name"] for r in s.rooms if r["id"] == s.current_room), "Main room")

    def _keep_current(self):
        """What's around you now goes back into its room (encrypted if it's a locked one)."""
        s = self.shell.store.get()
        if self.secret:
            room_id, key, name = self.secret
            vault.save(room_id, key, {"name": name, **self._extra, **self._snapshot(s)})
            self.secret, self._extra, self.safe = None, {}, None     # (the safe's keys are dropped too)
            return
        rooms = [{**r, **self._snapshot(s)} if r["id"] == s.current_room else r for r in s.rooms]
        self.shell.store.update(rooms=rooms)

    # -- moving between rooms ---------------------------------------------------------------------------
    def go(self, room_id):
        self._keep_current()
        room = next((r for r in self.shell.store.get().rooms if r["id"] == room_id), None)
        if room is not None:
            self._load(room)
            self.shell.toast(room["name"])

    def new_room(self, name=None):
        s = self.shell.settings()
        taken = {r["name"] for r in s.rooms}
        name = name or next((n for n in ROOM_NAMES if n not in taken), f"Room {len(s.rooms) + 1}")
        self._keep_current()
        room = {"id": uuid.uuid4().hex[:8], "name": name, "panels": [], "selected": 0, "layout": "space",
                "environment": self.shell.settings().environment}
        self.shell.store.update(rooms=self.shell.store.get().rooms + [room])
        self._load(room)
        self.shell.toast(f"New room: {name}")

    def delete_room(self, room_id):
        s = self.shell.settings()
        if room_id == "home" or len(s.rooms) < 2:
            return
        if room_id == s.current_room:
            self.go("home")
        self.shell.store.update(rooms=[r for r in self.shell.store.get().rooms if r["id"] != room_id])

    def lock(self):
        """Leave the locked room (it's saved encrypted): back to Home."""
        if self.secret:
            self.go("home")

    # -- locked rooms -----------------------------------------------------------------------------------
    def create_locked(self, pin, on_done):
        """A new hidden locked room with this PIN, empty; on_done(recovery key or None) on the render thread."""
        self.busy = True

        def make():
            room_id = uuid.uuid4().hex[:12]
            try:
                recovery = vault.create(room_id, pin, {"name": "Safe", "panels": [], "selected": 0,
                                                       "layout": "space", "environment": ""})
                key = vault.open_room(room_id, pin)
                content = vault.load(room_id, key)       # its safe opens with the recovery key (Cryptomator too)
                vault.save(room_id, key, {**content, "vault_password": recovery})
            except Exception:
                log_error("make a locked room")
                recovery = key = None
            self.shell.later(lambda: self._made(room_id, key, recovery, on_done))

        threading.Thread(target=make, daemon=True, name="room-lock").start()

    def _made(self, room_id, key, recovery, on_done):
        self.busy = False
        if key is not None:
            self._enter(room_id, key)
        on_done(recovery)

    def try_code(self, code, on_result):
        """Open the locked room this code (PIN or recovery key) belongs to; on_result(True / False / "wait")."""
        if time.monotonic() < self._wait_until:
            on_result("wait")
            return
        self.busy = True

        def find():
            found = vault.find_room(code)
            self.shell.later(lambda: self._found(found, on_result))

        threading.Thread(target=find, daemon=True, name="room-unlock").start()

    def _found(self, found, on_result):
        self.busy = False
        if found is None:
            self._fails += 1
            if self._fails >= FAILS_BEFORE_WAIT:
                self._fails, self._wait_until = 0, time.monotonic() + WAIT_SECONDS
            on_result(False)
            return
        self._fails = 0
        self._enter(*found)
        on_result(True)

    def _enter(self, room_id, key):
        try:
            content = vault.load(room_id, key)
        except Exception:
            log_error("open a locked room")
            return
        self._keep_current()
        self._load(content, room_id=room_id)
        self.secret = (room_id, key, content.get("name", "Safe"))
        self._extra = {k: v for k, v in content.items() if k not in ROOM_KEYS and k != "name"}
        if "vault_password" not in self._extra:         # (a room made before safes existed)
            self._extra["vault_password"] = vault.new_recovery_key()
        threading.Thread(target=self._open_safe, args=(room_id, self._extra["vault_password"]), daemon=True,
                         name="room-safe").start()
        self.shell.toast(f"{self.secret[2]} - locked room")

    def _open_safe(self, room_id, password):
        """The room's safe: its files, encrypted in Cryptomator's format (open it with Cryptomator too: the
        folder rooms/<id>.vault, the password = the room's recovery key)."""
        from ..core import cryptomator
        folder = vault.FOLDER / f"{room_id}.vault"
        try:
            safe = (cryptomator.unlock(folder, password) if (folder / "masterkey.cryptomator").exists()
                    else cryptomator.create(folder, password))
        except Exception:
            log_error("open the room's safe")
            return
        self.shell.later(lambda: self._safe_opened(room_id, safe))

    def _safe_opened(self, room_id, safe):
        if self.secret and self.secret[0] == room_id:
            self.safe = safe

    def _before_save(self, data):
        """settings.json never holds a locked room: Home's things are written instead (the locked room is saved
        encrypted, right now)."""
        if not self.secret:
            return data
        room_id, key, name = self.secret
        s = self.shell.store.get()
        try:
            vault.save(room_id, key, {"name": name, **self._extra, **self._snapshot(s)})
        except Exception:
            log_error("save the locked room")
        home = next((r for r in data.get("rooms", []) if r["id"] == "home"), None) or {}
        data = dict(data)
        for k in ROOM_KEYS:
            if k in home:
                data[k] = home[k]
        data["current_room"] = "home"
        return data

    # -- the secret entrance ----------------------------------------------------------------------------
    def clock_tapped(self):
        now = time.monotonic()
        self._taps = [t for t in self._taps if now - t < SECRET_SECONDS] + [now]
        if len(self._taps) >= SECRET_TAPS:
            self._taps = []
            self.shell.open_keypad("unlock")


class Keypad(View):
    """A PIN pad in VR: digits as dots, 0-9, delete, OK. mode "unlock" (a PIN or the 24-digit recovery key),
    "new" (choose a PIN), "again" (the same PIN again)."""

    def __init__(self, shell, mode, on_code, message=""):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell, self.mode, self.on_code = shell, mode, on_code
        self.code, self.message = "", message

    def state(self):
        return self.code, self.message, self.shell.rooms.busy

    def _press(self, digit):
        if len(self.code) < (vault.RECOVERY_DIGITS if self.mode == "unlock" else PIN_MAX):
            self.code += digit
            self.message = ""

    def _back(self):
        self.code = self.code[:-1]

    def _ok(self):
        if self.mode != "unlock" and len(self.code) < PIN_MIN:
            self.message = f"At least {PIN_MIN} digits"
            return
        code, self.code = self.code, ""
        self.on_code(code)

    def build(self):
        title = {"unlock": "Enter code", "new": "Choose a PIN for the locked room",
                 "again": "The same PIN again"}[self.mode]
        dots = " ".join("●" for _ in self.code) or " "
        if self.mode == "unlock" and len(self.code) > PIN_MAX:     # a recovery key: in groups of 4
            dots = "-".join(self.code[i:i + 4] for i in range(0, len(self.code), 4))
        size = theme.ROW + 40
        keys = [Row([Button(f"key:{d}", d, w=size * 2, h=size, size=theme.TITLE, style="normal",
                            on_click=lambda d=d: self._press(d)) for d in row], gap=14)
                for row in ("123", "456", "789")]
        keys.append(Row([Button("key:back", None, Icon.BACK, w=size * 2, h=size, style="flat", size=theme.TITLE,
                                on_click=self._back),
                         Button("key:0", "0", w=size * 2, h=size, size=theme.TITLE, style="normal",
                                on_click=lambda: self._press("0")),
                         Button("key:ok", "OK", w=size * 2, h=size, size=theme.TITLE - 6, style="primary",
                                on_click=self._ok)], gap=14))
        busy = self.shell.rooms.busy
        note = "Opening..." if busy else self.message
        hint = ("A PIN, or the 24-digit recovery key" if self.mode == "unlock"
                else f"{PIN_MIN}-{PIN_MAX} digits - 6 or more is much safer")
        return Panel([Label(title, size=theme.TITLE - 6, h=theme.ROW, align="center", w=size * 6 + 28),
                      Label(dots, size=theme.TITLE, color=theme.ACCENT_LIGHT, h=theme.ROW + 10, align="center",
                            w=size * 6 + 28),
                      *keys,
                      Label(note or hint, size=theme.SMALL - 6, color=theme.ACCENT_LIGHT if note else theme.MUTED,
                            h=theme.ROW - 10, align="center", w=size * 6 + 28),
                      Row([Spacer(flex=1), Button("key:cancel", "Cancel", Icon.CLOSE, h=theme.ROW - 8, style="flat",
                                                  size=theme.TEXT - 8, on_click=self.shell.close_dialog)],
                          w=size * 6 + 28)], pad=28, gap=14)


class RecoveryKey(View):
    """The recovery key of a new locked room, shown once: write it down."""

    def __init__(self, shell, key):
        super().__init__(distance=DIALOG_DISTANCE)
        self.shell, self.key = shell, key

    def build(self):
        w = 1500
        return Panel([Label("Your recovery key", size=theme.TITLE, w=w, h=theme.ROW + 10),
                      Label("If you forget the PIN, this opens the room. Write it down now - it's shown only once,",
                            size=theme.SMALL - 4, color=theme.MUTED, w=w, h=theme.ROW - 16),
                      Label("and without the PIN or this key nobody can open the room, not even you.",
                            size=theme.SMALL - 4, color=theme.MUTED, w=w, h=theme.ROW - 16),
                      Label(self.key, size=theme.HUGE - 6, color=theme.ACCENT_LIGHT, w=w, h=theme.ROW + 50,
                            align="center"),
                      Row([Spacer(flex=1), Button("recovery:ok", "I wrote it down", Icon.PIN, h=theme.ROW,
                                                  style="primary", size=theme.TEXT - 4,
                                                  on_click=self.shell.close_dialog)], w=w)], pad=36, gap=12)
