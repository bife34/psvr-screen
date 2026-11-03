"""Moving and copying files between folders - and into / out of a locked room's safe (encrypted, in
Cryptomator's format). Runs in the background: big files take a while.

    into the safe    encrypted into it; "move" then deletes the original for good (not the Recycle Bin, where
                     anyone could take it back out - note that a disk may keep traces of deleted files)
    out of the safe  decrypted into the folder ("Take out")
"""
import shutil
from pathlib import Path

from ..video.files import in_vault


def free_name(path):
    """`path`, or "name (2).ext" if something is already called that."""
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def transfer(how, path, target):
    """Move / copy file or folder `path` into folder `target` (either may be in a safe). Returns where it went."""
    to = free_name(target / path.name)
    if not in_vault(path) and not in_vault(target):
        if how == "move":
            shutil.move(str(path), str(to))
        elif path.is_dir():
            shutil.copytree(path, to)
        else:
            shutil.copy2(path, to)
        return to
    _copy(path, to)
    if how == "move":
        delete_for_good(path)
    return to


def _copy(path, to):
    if path.is_dir():
        if in_vault(to):
            to.vault.mkdir(to)
        else:
            to.mkdir(parents=True, exist_ok=True)
        for child in (path.vault.listdir(path) if in_vault(path) else path.iterdir()):
            child = child.path if in_vault(path) else child
            _copy(child, to / child.name)
        return
    source = path.vault.open(path) if in_vault(path) else open(path, "rb")
    with source:
        if in_vault(to):
            to.vault.write(to, source)
        else:
            with open(to, "wb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)


def delete_for_good(path):
    if in_vault(path):
        path.vault.remove(path)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        Path(path).unlink()
