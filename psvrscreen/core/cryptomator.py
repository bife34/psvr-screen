"""Encrypted vaults in Cryptomator's own format (vault format 8, "SIV_GCM") - a vault made here can also be opened
with the Cryptomator apps (PC, phone) using its password, and the other way round. Built from Cryptomator's
published specification (docs.cryptomator.org, "Security Architecture"):

    masterkey.cryptomator   the two vault keys (encryption + MAC), wrapped (RFC 3394) with a key made from the
                            password by scrypt (N=32768, r=8, p=1)
    vault.cryptomator       the vault's settings, signed (HS256 JWT) with the keys
    d/XX/YYYY.../           every folder's contents; its name = base32(SHA1(AES-SIV(folder id)))
      <name>.c9r            a file: its name encrypted with AES-SIV (the folder id as associated data), base64url
      <name>.c9r/dir.c9r    a sub-folder: holds its folder id
      <name>.c9s/           a name longer than 220 characters: shortened (name.c9s holds the long name)
    file contents           a 68-byte header (a random content key, AES-GCM) then 32 KiB chunks, each AES-GCM
                            with the chunk's number and the header nonce as associated data - so a video can
                            start anywhere without reading the whole file

Nothing here writes a readable copy anywhere: reading gives bytes / a file-like object in memory.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import unicodedata
import uuid
from pathlib import Path

SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1
CHUNK = 32 * 1024
CHUNK_ENC = 12 + CHUNK + 16
HEADER = 12 + 40 + 16
SHORTENING = 220


class WrongPassword(Exception):
    pass


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii")


def _jwt(header, payload, key):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    signing = enc(header) + b"." + enc(payload)
    sig = base64.urlsafe_b64encode(hmac.new(key, signing, hashlib.sha256).digest()).rstrip(b"=")
    return (signing + b"." + sig).decode("ascii")


def _kek(password, salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    password = unicodedata.normalize("NFC", password)
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode("utf-8"))


def create(folder, password):
    """A new empty vault in `folder` (made if needed). Returns the opened Vault."""
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    enc_key, mac_key = os.urandom(32), os.urandom(32)
    salt = os.urandom(32)
    kek = _kek(password, salt)
    version = 999
    masterkey = {
        "version": version, "scryptSalt": _b64(salt), "scryptCostParam": SCRYPT_N, "scryptBlockSize": SCRYPT_R,
        "primaryMasterKey": _b64(aes_key_wrap(kek, enc_key)), "hmacMasterKey": _b64(aes_key_wrap(kek, mac_key)),
        "versionMac": _b64(hmac.new(mac_key, version.to_bytes(4, "big"), hashlib.sha256).digest()),
    }
    (folder / "masterkey.cryptomator").write_text(json.dumps(masterkey, indent=2), encoding="utf-8")
    token = _jwt({"kid": "masterkeyfile:masterkey.cryptomator", "typ": "JWT", "alg": "HS256"},
                 {"format": 8, "shorteningThreshold": SHORTENING, "jti": str(uuid.uuid4()), "cipherCombo": "SIV_GCM"},
                 enc_key + mac_key)
    (folder / "vault.cryptomator").write_text(token, encoding="ascii")
    vault = Vault(folder, enc_key, mac_key)
    vault._dir_path("").mkdir(parents=True, exist_ok=True)
    return vault


def unlock(folder, password):
    """The Vault in `folder` with this password; raises WrongPassword."""
    from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap
    folder = Path(folder)
    masterkey = json.loads((folder / "masterkey.cryptomator").read_text(encoding="utf-8"))
    kek = _kek_from(masterkey, password)
    try:
        enc_key = aes_key_unwrap(kek, base64.b64decode(masterkey["primaryMasterKey"]))
        mac_key = aes_key_unwrap(kek, base64.b64decode(masterkey["hmacMasterKey"]))
    except InvalidUnwrap:
        raise WrongPassword() from None
    return Vault(folder, enc_key, mac_key)


def _kek_from(masterkey, password):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=base64.b64decode(masterkey["scryptSalt"]), length=32, n=masterkey["scryptCostParam"],
                  r=masterkey["scryptBlockSize"], p=1).derive(unicodedata.normalize("NFC", password).encode("utf-8"))


class Entry:
    """A file or folder in the vault (what a listing gives)."""

    def __init__(self, vault, parent, name, is_dir, size=0, mtime=0.0):
        self.vault, self.parent, self.name = vault, parent, name     # parent: the folder's VaultPath
        self._dir, self.size, self.mtime = is_dir, size, mtime

    @property
    def path(self):
        return self.parent / self.name

    def is_dir(self):
        return self._dir


class VaultPath:
    """A place in a vault: "/" and names below it, like pathlib's paths (a small part of them)."""

    def __init__(self, vault, parts=()):
        self.vault, self.parts = vault, tuple(parts)

    def __truediv__(self, name):
        return VaultPath(self.vault, self.parts + (name,))

    @property
    def name(self):
        return self.parts[-1] if self.parts else ""

    @property
    def parent(self):
        return VaultPath(self.vault, self.parts[:-1])

    @property
    def suffix(self):
        name = self.name
        return name[name.rfind("."):] if "." in name[1:] else ""

    @property
    def stem(self):
        return self.name[:len(self.name) - len(self.suffix)] if self.suffix else self.name

    def __eq__(self, other):
        return isinstance(other, VaultPath) and other.vault is self.vault and other.parts == self.parts

    def __hash__(self):
        return hash(("vault", id(self.vault), self.parts))

    def __str__(self):
        return "vault:/" + "/".join(self.parts)

    __repr__ = __str__

    def is_relative_to(self, other):
        return isinstance(other, VaultPath) and self.parts[:len(other.parts)] == other.parts

    def is_dir(self):
        return self.vault.is_dir(self)

    def is_file(self):
        return self.vault.entry(self) is not None and not self.is_dir()

    def exists(self):
        return not self.parts or self.vault.entry(self) is not None

    def stat(self):
        entry = self.vault.entry(self)
        if entry is None:
            raise FileNotFoundError(str(self))
        return _Stat(entry.size, entry.mtime)

    @property
    def parents(self):
        return [VaultPath(self.vault, self.parts[:i]) for i in range(len(self.parts) - 1, -1, -1)]

    def with_name(self, name):
        return self.parent / name

    anchor = "vault:"


class _Stat:
    def __init__(self, size, mtime):
        self.st_size, self.st_mtime, self.st_file_attributes = size, mtime, 0


class Vault:
    def __init__(self, folder, enc_key, mac_key):
        from cryptography.hazmat.primitives.ciphers.aead import AESSIV
        self.folder = Path(folder)
        self._enc, self._mac = enc_key, mac_key
        self._siv = AESSIV(mac_key + enc_key)          # (RFC 5297: the S2V key first, then the CTR key)
        self._dir_ids = {(): ""}                        # vault path parts -> folder id
        self._listings = {}                             # folder parts -> [Entry] (until something changes)

    @property
    def root(self):
        return VaultPath(self)

    # -- names and folders ----------------------------------------------------------------------------
    def _dir_path(self, dir_id):
        digest = hashlib.sha1(self._siv.encrypt(dir_id.encode("utf-8"), None)).digest()
        name = base64.b32encode(digest).decode("ascii")
        return self.folder / "d" / name[:2] / name[2:]

    def _enc_name(self, name, dir_id):
        name = unicodedata.normalize("NFC", name)      # (as Cryptomator does)
        return _b64url(self._siv.encrypt(name.encode("utf-8"), [dir_id.encode("utf-8")]))

    def _dec_name(self, enc, dir_id):
        return self._siv.decrypt(base64.urlsafe_b64decode(enc), [dir_id.encode("utf-8")]).decode("utf-8")

    def _node(self, dir_id, name):
        """Where an entry called `name` lives in the folder with this id (shortened if the name is long)."""
        enc = self._enc_name(name, dir_id) + ".c9r"
        if len(enc) > SHORTENING:
            short = _b64url(hashlib.sha1(enc.encode("ascii")).digest()) + ".c9s"
            return self._dir_path(dir_id) / short, enc
        return self._dir_path(dir_id) / enc, None

    def _id_of(self, path):
        """The folder id of a vault folder (None: it isn't one)."""
        parts = path.parts
        if parts in self._dir_ids:
            return self._dir_ids[parts]
        parent = self._id_of(path.parent)
        if parent is None:
            return None
        node, _ = self._node(parent, path.name)
        try:
            dir_id = (node / "dir.c9r").read_text(encoding="utf-8")
        except OSError:
            return None
        self._dir_ids[parts] = dir_id
        return dir_id

    def is_dir(self, path):
        return self._id_of(path) is not None

    def entry(self, path):
        """The Entry of a file / folder (None: not there)."""
        if not path.parts:
            return None
        return next((e for e in self.listdir(path.parent) if e.name == path.name), None)

    def _changed(self, path):
        self._listings.pop(path.parent.parts, None)
        self._listings.pop(path.parts, None)

    def listdir(self, path):
        """[Entry] of a vault folder, folders first, by name."""
        if path.parts in self._listings:
            return self._listings[path.parts]
        out = self._list(path)
        self._listings[path.parts] = out
        return out

    def _list(self, path):
        dir_id = self._id_of(path)
        if dir_id is None:
            return []
        out = []
        try:
            nodes = list(self._dir_path(dir_id).iterdir())
        except OSError:
            return []
        for node in nodes:
            try:
                if node.suffix == ".c9s":
                    enc = (node / "name.c9s").read_text(encoding="ascii")
                    content = node / "contents.c9r"
                elif node.suffix == ".c9r":
                    enc, content = node.name, node
                else:
                    continue
                name = self._dec_name(enc[:-4], dir_id)
                if (node / "dir.c9r").is_file():
                    out.append(Entry(self, path, name, True))
                else:
                    st = content.stat()
                    out.append(Entry(self, path, name, False, _plain_size(st.st_size), st.st_mtime))
            except Exception:
                continue           # (a damaged entry, or one this key can't read)
        out.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        return out

    def mkdir(self, path):
        """A new folder (and the ones it's in)."""
        if self.is_dir(path):
            return
        if path.parts:
            self.mkdir(path.parent)
        parent = self._id_of(path.parent)
        node, long_name = self._node(parent, path.name)
        node.mkdir(parents=True, exist_ok=True)
        if long_name:
            (node / "name.c9s").write_text(long_name, encoding="ascii")
        dir_id = str(uuid.uuid4())
        (node / "dir.c9r").write_text(dir_id, encoding="utf-8")
        self._changed(path)
        self._dir_path(dir_id).mkdir(parents=True, exist_ok=True)
        self._dir_ids[path.parts] = dir_id

    # -- file contents ----------------------------------------------------------------------------------
    def _content_node(self, path):
        parent = self._id_of(path.parent)
        if parent is None:
            raise FileNotFoundError(str(path))
        node, long_name = self._node(parent, path.name)
        return (node / "contents.c9r" if long_name else node), node, long_name

    def write(self, path, source):
        """Encrypt `source` (a readable binary file object) into the vault as `path`."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.mkdir(path.parent)
        content, node, long_name = self._content_node(path)
        if long_name:
            node.mkdir(parents=True, exist_ok=True)
            (node / "name.c9s").write_text(long_name, encoding="ascii")
        header_nonce, content_key = os.urandom(12), os.urandom(32)
        header = header_nonce + AESGCM(self._enc).encrypt(header_nonce, b"\xff" * 8 + content_key, None)
        gcm = AESGCM(content_key)
        part = content.with_name(content.name + ".part")
        with open(part, "wb") as out:
            out.write(header)
            n = 0
            while True:                                 # (an empty file: the header only)
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                nonce = os.urandom(12)
                out.write(nonce + gcm.encrypt(nonce, chunk, n.to_bytes(8, "big") + header_nonce))
                n += 1
                if len(chunk) < CHUNK:
                    break
        part.replace(content)
        self._changed(path)

    def open(self, path):
        """A seekable, read-only file object of the file's plain contents (decrypted as it's read)."""
        content, _, _ = self._content_node(path)
        return _Reader(self, content)

    def read_bytes(self, path):
        with self.open(path) as f:
            return f.read()

    def remove(self, path):
        """Delete a file or a whole folder from the vault."""
        import shutil
        self._changed(path)
        if self.is_dir(path):
            for entry in self.listdir(path):
                self.remove(entry.path)
            dir_id = self._id_of(path)
            shutil.rmtree(self._dir_path(dir_id), ignore_errors=True)
            node, _ = self._node(self._id_of(path.parent), path.name)
            shutil.rmtree(node, ignore_errors=True)
            self._dir_ids.pop(path.parts, None)
            return
        content, node, long_name = self._content_node(path)
        if long_name:
            shutil.rmtree(node, ignore_errors=True)
        else:
            content.unlink(missing_ok=True)


def _plain_size(size):
    if size < HEADER:
        return 0
    body = size - HEADER
    chunks, rest = divmod(body, CHUNK_ENC)
    return chunks * CHUNK + max(0, rest - 28)


class _Reader(io.RawIOBase):
    """Plain bytes of a vault file, decrypted chunk by chunk as they're read; seekable (videos)."""

    def __init__(self, vault, content):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        super().__init__()
        self._f = open(content, "rb")
        header = self._f.read(HEADER)
        nonce = header[:12]
        payload = AESGCM(vault._enc).decrypt(nonce, header[12:], None)
        self._nonce, self._gcm = nonce, AESGCM(payload[8:40])
        self._size = _plain_size(os.fstat(self._f.fileno()).st_size)
        self._pos, self._cache = 0, (None, b"")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def _chunk(self, n):
        if self._cache[0] == n:
            return self._cache[1]
        self._f.seek(HEADER + n * CHUNK_ENC)
        blob = self._f.read(CHUNK_ENC)
        plain = self._gcm.decrypt(blob[:12], blob[12:], n.to_bytes(8, "big") + self._nonce) if blob else b""
        self._cache = (n, plain)
        return plain

    def readinto(self, buffer):
        if self._pos >= self._size:
            return 0
        n, offset = divmod(self._pos, CHUNK)
        data = self._chunk(n)[offset:offset + len(buffer)]
        buffer[:len(data)] = data
        self._pos += len(data)
        return len(data)

    def read(self, size=-1):
        if size is None or size < 0:
            size = self._size - self._pos
        out = bytearray()
        while size > 0 and self._pos < self._size:
            n, offset = divmod(self._pos, CHUNK)
            data = self._chunk(n)[offset:offset + size]
            if not data:
                break
            out += data
            self._pos += len(data)
            size -= len(data)
        return bytes(out)

    def close(self):
        try:
            self._f.close()
        finally:
            super().close()
