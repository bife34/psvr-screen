"""The assistant: what you say goes to the brain (ai/brain.py) when it isn't a plain command, and the actions it
answers with are done here - by the same code as the spoken commands (voice_commands.py), so they behave
exactly like them. Render thread.

    showing(shell)          what's in front of you, for the brain ("video", "photo wall", "windows"...)
    run(shell, actions)     do them; returns what to show ("Bigger · Farther")
"""
from pathlib import Path

from ..video import files
from . import voice_commands

PHRASES = {   # action -> what voice_commands understands
    ("size", "bigger"): ["bigger"], ("size", "smaller"): ["smaller"],
    ("size", "much_bigger"): ["bigger", "bigger", "bigger"], ("size", "much_smaller"): ["smaller", "smaller", "smaller"],
    ("distance", "closer"): ["closer"], ("distance", "farther"): ["farther"],
    ("distance", "much_closer"): ["closer", "closer", "closer"], ("distance", "much_farther"): ["much farther"],
}
WINDOW_STEPS = {"bigger": ("width", 0.3), "smaller": ("width", -0.3), "much_bigger": ("width", 1.0),
                "much_smaller": ("width", -1.0), "closer": ("distance", -0.4), "farther": ("distance", 0.4),
                "much_closer": ("distance", -1.2), "much_farther": ("distance", 1.5)}
FOLDER_NAMES = {   # spoken name -> the folder's own names (English / Portuguese Windows)
    "downloads": ("Downloads", "Transferências"), "desktop": ("Desktop", "Ambiente de Trabalho"),
    "documents": ("Documents", "Documentos"), "pictures": ("Pictures", "Imagens"), "photos": ("Pictures", "Imagens"),
    "videos": ("Videos", "Vídeos"), "music": ("Music", "Música"),
    "transferencias": ("Downloads", "Transferências"), "ambiente de trabalho": ("Desktop", "Ambiente de Trabalho"),
    "documentos": ("Documents", "Documentos"), "imagens": ("Pictures", "Imagens"), "fotos": ("Pictures", "Imagens"),
    "musica": ("Music", "Música"),
}


def showing(shell):
    if shell.video is not None:
        return "photo" if shell.video.photo else "video"
    if shell.timeline is not None:
        return "timeline"
    if shell.wall is not None:
        return f"photo wall ({shell.settings().wall_shape})"
    if shell.dialog is not None or shell.menu is not None:
        return "a menu"
    return "windows"


def run(shell, actions):
    done = []
    for a in actions:
        what = _one(shell, a)
        if what:
            done.append(what)
    return "  ·  ".join(done)


def _say(shell, *phrases):
    out = ""
    for p in phrases:
        out = voice_commands.handle(shell, p, catch_all=False) or out
    return out


def _one(shell, a):
    do = a.get("do")
    if do == "reply":
        return a.get("text", "")
    if do == "open":
        app = a.get("app")
        if app in ("home", "settings", "apps"):
            shell.dock_action({"settings": "global_settings"}.get(app, app))
            return app.capitalize()
        shell.stop_video()
        shell.open_native(app)
        return "Opening " + app
    if do == "open_folder":
        folder = find_folder(a.get("folder", ""))
        if folder is None:
            return f"I couldn't find a folder called \"{a.get('folder', '')}\""
        shell.stop_video()
        shell.watch_wall(folder)
        return "Opening " + folder.name
    if do == "find_photos":
        return _say(shell, "find " + a.get("query", "")) or "Looking..."
    if do == "wall_shape":
        if shell.wall is None:     # the wall opens in that shape
            shell.store.update(wall_shape=a.get("shape"))
            shell.watch_wall(voice_commands._pictures_folder())
            return a.get("shape", "").capitalize()
        shell.wall.set_shape(a.get("shape"))
        return a.get("shape", "").capitalize()
    if do in ("size", "distance"):
        change = a.get("change")
        if a.get("target") == "window" or (shell.video is None and shell.wall is None):
            s = shell.settings()
            if not s.panels:
                return ""
            key, step = WINDOW_STEPS[change]
            shell.store.nudge(key, step)
            return change.replace("_", " ").capitalize()
        return _say(shell, *PHRASES[(do, change)])
    if do == "player":
        command = a.get("command")
        phrase = {"play": "play", "pause": "pause", "next": "next", "previous": "previous", "louder": "louder",
                  "quieter": "quieter", "mute": "mute", "unmute": "unmute", "forward": "forward",
                  "rewind": "rewind"}.get(command)
        return _say(shell, phrase) if phrase else ""
    if do == "recenter":
        return _say(shell, "recenter")
    if do == "close":
        if a.get("what") == "menu":
            shell.close_menu()
            shell._close_dialog()
            return "Closed"
        if a.get("what") == "window" and shell.video is None and shell.wall is None and shell.timeline is None:
            s = shell.settings()
            if s.panels:
                shell.window_action(s.panels[s.selected]["id"], "close")
                return "Closed the window"
            return ""
        return _say(shell, "exit")
    if do == "power":
        shell.quit("restart" if a.get("what") == "restart" else "off")
        return "Restarting..." if a.get("what") == "restart" else "Turning off..."
    return ""


def find_folder(name):
    """A spoken folder name -> the folder: your main folders by name (any language), a path, or a folder of
    that name in one of them."""
    text = _plain(name.strip().strip("\"'"))
    text = text.removeprefix("my ").removeprefix("the ").removesuffix(" folder").strip()
    if not text:
        return None
    path = Path(name.strip().strip("\"'"))
    if path.is_absolute() and path.is_dir():       # a whole path ("F:\fotos")
        return path
    home = files.home_folders()
    for spoken, real in FOLDER_NAMES.items():
        if text == _plain(spoken.strip()) or text.rstrip("s") == _plain(spoken.strip()).rstrip("s"):
            for folder in home:
                if folder.name in real:
                    return folder
    for folder in home:
        if _plain(folder.name) == text:
            return folder
    for base in home:   # one level down: "my holiday folder"
        try:
            for child in base.iterdir():
                if child.is_dir() and text in _plain(child.name):
                    return child
        except OSError:
            continue
    return None


def _plain(text):
    """Lower case without accents: "Transferências" -> "transferencias"."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
