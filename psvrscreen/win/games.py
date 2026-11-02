"""The PC's installed games (Steam), with their cover pictures, and starting them.

Steam: its libraries (steamapps/libraryfolders.vdf) hold one appmanifest_<id>.acf per installed app; the
cover pictures are in Steam's appcache/librarycache/<id>/. Tools that aren't games (Steam's own runtimes,
drivers, desktop utilities) are left out.
"""
import os
import re
import winreg
from dataclasses import dataclass
from pathlib import Path

NOT_GAMES = {"228980", "250820", "1070560", "1391110", "1628350", "1493710",   # Steam runtimes, SteamVR, Proton
             "992490", "1494460", "1091650", "967130", "431960"}              # iVRy, Desktop+, mouse / widget tools
NOT_GAME_WORDS = ("redistributable", "driver for steamvr", "dedicated server", " sdk", "soundtrack")


@dataclass
class Game:
    id: str              # "steam:<appid>"
    name: str
    folder: str          # where it's installed (its windows' programs are in there)
    cover: str = ""      # a picture file, or ""

    def start(self):
        os.startfile(f"steam://rungameid/{self.id.split(':', 1)[1]}")


def _steam_path():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return None


def _acf_value(text, key):
    match = re.search(r'"%s"\s+"([^"]*)"' % key, text)
    return match.group(1) if match else ""


def _cover(steam, appid):
    """A wide picture of the game: its header, else a wide picture in its cache folder."""
    folder = steam / "appcache" / "librarycache" / appid
    for name in ("header.jpg", "library_hero.jpg"):
        if (folder / name).is_file():
            return str(folder / name)
    flat = steam / "appcache" / "librarycache" / f"{appid}_header.jpg"
    if flat.is_file():
        return str(flat)
    try:
        from PIL import Image
        best = None
        for path in folder.rglob("*.jpg"):
            with Image.open(path) as image:
                w, h = image.size
            if 1.8 <= w / h <= 2.4 and (best is None or w > best[0]):
                best = (w, str(path))
        return best[1] if best else ""
    except OSError:
        return ""


def steam_games():
    steam = _steam_path()
    if steam is None:
        return []
    libraries = [steam]
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    except OSError:
        pass
    games, seen = [], set()
    for library in libraries:
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            appid, name = _acf_value(text, "appid"), _acf_value(text, "name").strip()
            if not appid or appid in seen or appid in NOT_GAMES or any(w in name.lower() for w in NOT_GAME_WORDS):
                continue
            seen.add(appid)
            folder = library / "steamapps" / "common" / _acf_value(text, "installdir")
            games.append(Game("steam:" + appid, name, str(folder), _cover(steam, appid)))
    return sorted(games, key=lambda g: g.name.lower())


def installed_games():
    """Every installed game found, by name."""
    try:
        return steam_games()
    except Exception:
        return []
