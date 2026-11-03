"""Locked rooms: what's in them is kept encrypted on disk, readable only with the room's PIN or its recovery key.

    room key      32 random bytes - encrypts the room (AES-256-GCM: nobody can read or change it unnoticed)
    PIN           -> scrypt (slow on purpose, ~0.5 s and 256 MB per try) -> a key that wraps the room key
    recovery key  24 random digits (shown once, to write down) -> scrypt -> another wrap of the same room key

A room's files in DATA_DIR/rooms: <id>.lock (salts and the two wrapped keys, JSON) and <id>.room (the room,
encrypted). Without the PIN or the recovery key there is no way in - not for anyone. A 4-digit PIN is only 10 000
tries for someone who copies the files: longer is safer (the app says so).
"""
import base64
import json
import os
import secrets

from .paths import DATA_DIR

FOLDER = DATA_DIR / "rooms"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 18, 8, 1
RECOVERY_DIGITS = 24


class WrongCode(Exception):
    pass


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(text):
    return base64.b64decode(text.encode("ascii"))


def _derive(code, salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(code.encode("utf-8"))


def _wrap(key, secret, label):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return {"nonce": _b64(nonce), "key": _b64(AESGCM(key).encrypt(nonce, secret, label))}


def _unwrap(key, wrapped, label):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        return AESGCM(key).decrypt(_unb64(wrapped["nonce"]), _unb64(wrapped["key"]), label)
    except InvalidTag:
        raise WrongCode() from None


def new_recovery_key():
    """24 digits in groups of 4 ("1234-5678-..."): typed on the same keypad as the PIN."""
    digits = "".join(str(secrets.randbelow(10)) for _ in range(RECOVERY_DIGITS))
    return "-".join(digits[i:i + 4] for i in range(0, RECOVERY_DIGITS, 4))


def _digits(code):
    return "".join(c for c in code if c.isdigit())


def create(room_id, pin, content):
    """A new locked room: encrypt `content` (a dict). Returns its recovery key (show it once)."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    room_key = os.urandom(32)
    recovery = new_recovery_key()
    lock = {"version": 1, "scrypt": [SCRYPT_N, SCRYPT_R, SCRYPT_P]}
    for name, code in (("pin", _digits(pin)), ("recovery", _digits(recovery))):
        salt = os.urandom(16)
        lock[name] = {"salt": _b64(salt), **_wrap(_derive(code, salt), room_key, room_id.encode())}
    (FOLDER / f"{room_id}.lock").write_text(json.dumps(lock), encoding="utf-8")
    save(room_id, room_key, content)
    return recovery


def room_ids():
    """The locked rooms there are (their ids)."""
    if not FOLDER.is_dir():
        return []
    return [p.stem for p in FOLDER.glob("*.lock")]


def open_room(room_id, code):
    """The room key for this PIN or recovery key; raises WrongCode."""
    lock = json.loads((FOLDER / f"{room_id}.lock").read_text(encoding="utf-8"))
    code = _digits(code)
    which = "recovery" if len(code) == RECOVERY_DIGITS else "pin"
    entry = lock[which]
    return _unwrap(_derive(code, _unb64(entry["salt"])), entry, room_id.encode())


def find_room(code):
    """(room id, room key) of the locked room this code opens, or None (every room is tried)."""
    for room_id in room_ids():
        try:
            return room_id, open_room(room_id, code)
        except (WrongCode, OSError, KeyError, ValueError):
            continue
    return None


def load(room_id, room_key):
    """The room's content (dict)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = (FOLDER / f"{room_id}.room").read_bytes()
    return json.loads(AESGCM(room_key).decrypt(blob[:12], blob[12:], room_id.encode()).decode("utf-8"))


def save(room_id, room_key, content):
    """Encrypt the room's content to disk (a new nonce every time; written whole, then swapped in)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    FOLDER.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    data = nonce + AESGCM(room_key).encrypt(nonce, json.dumps(content).encode("utf-8"), room_id.encode())
    part = FOLDER / f"{room_id}.room.part"
    part.write_bytes(data)
    part.replace(FOLDER / f"{room_id}.room")


def change_pin(room_id, room_key, new_pin):
    lock_path = FOLDER / f"{room_id}.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    salt = os.urandom(16)
    lock["pin"] = {"salt": _b64(salt), **_wrap(_derive(_digits(new_pin), salt), room_key, room_id.encode())}
    lock_path.write_text(json.dumps(lock), encoding="utf-8")


def delete(room_id):
    for suffix in (".lock", ".room"):
        try:
            (FOLDER / f"{room_id}{suffix}").unlink()
        except OSError:
            pass
