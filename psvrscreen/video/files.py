"""VR videos saved on the PC: find them, and what kind each one is (recognize.py: name tags, then picture).

Downloading is the usual way to watch videos from sites that don't let other players stream them.
"""
import os
from pathlib import Path

from . import recognize
from .info import VideoInfo, look_at_frames

EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
PICTURE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SEARCH_DEPTH = 2                 # folders inside the searched folders that are looked into
MAX_FILES = 300


def folders(names=("Downloads", "Videos", "Desktop", "Transferências", "Vídeos", "Ambiente de Trabalho")):
    """Where downloaded videos usually are: Downloads, Videos, Desktop (also the OneDrive ones)."""
    home = Path.home()
    found = []
    for base in (home, home / "OneDrive"):
        for name in names:
            path = base / name
            if path.is_dir() and path.resolve() not in [p.resolve() for p in found]:
                found.append(path)
    return found


def list_videos(extensions=EXTENSIONS, roots=None):
    """[(path, size in bytes, modified time)] of the video files (or `extensions`) found, newest first."""
    videos = []
    for root in roots or folders():
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - root_depth >= SEARCH_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
            for name in filenames:
                if Path(name).suffix.lower() in extensions:
                    path = Path(dirpath) / name
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    videos.append((path, stat.st_size, stat.st_mtime))
    videos.sort(key=lambda v: -v[2])
    return videos[:MAX_FILES]


def probe(path):
    """VideoInfo for a video file (always: an ordinary video plays on the screen). Reads a few frames."""
    import av
    with av.open(str(path)) as container:
        duration = float(container.duration or 0) / 1_000_000
        has_sound = bool(container.streams.audio)
    tags = recognize.from_tags(path.stem)
    try:
        layout = tags.merged(look_at_frames(str(path), duration))
    except Exception:
        layout = tags
    stereo = layout.stereo or "mono"
    projection = layout.projection or "screen"
    return VideoInfo(str(path), path.stem, projection, stereo, video_url=str(path),
                     audio_url=str(path) if has_sound else None, duration=duration, fov=layout.fov)


def show_in_folder(path):
    """Open Explorer on the PC with this file selected."""
    import subprocess
    subprocess.Popen(["explorer", "/select,", str(path)])


# -- browsing (the Videos app) ------------------------------------------------------------------------
def drives():
    """The PC's drives that are there: ["C:\\", "F:\\", ...]."""
    try:
        return [d for d in os.listdrives() if os.path.isdir(d)]
    except (AttributeError, OSError):
        return [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]


def folder_entries(folder, extensions=EXTENSIONS):
    """(sub-folders, video files - or `extensions`) of a folder, both sorted by name; hidden / system folders
    left out."""
    dirs, videos = [], []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_dir() and not entry.name.startswith((".", "$")):
                        dirs.append(Path(entry.path))
                    elif entry.is_file() and Path(entry.name).suffix.lower() in extensions:
                        videos.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        pass
    key = lambda p: p.name.lower()
    return sorted(dirs, key=key), sorted(videos, key=key)


def _vlc_recent():
    """Files VLC opened lately, newest first (its own list)."""
    from urllib.parse import unquote
    ini = Path(os.environ.get("APPDATA", "")) / "vlc" / "vlc-qt-interface.ini"
    try:
        text = ini.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("list=") and "file:///" in line:
            return [Path(unquote(u.strip()[len("file:///"):])) for u in line[5:].split(",") if u.strip().startswith("file:///")]
    return []


def _windows_recent():
    """Video files Windows lists as recently opened (its Recent folder's shortcuts), newest first."""
    folder = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    try:
        links = sorted((p for p in folder.glob("*.lnk") if Path(p.stem).suffix.lower() in EXTENSIONS),
                       key=lambda p: -p.stat().st_mtime)
    except OSError:
        return []
    try:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        return [Path(shell.CreateShortcut(str(link)).TargetPath) for link in links[:50]]
    except Exception:
        return []


def recent_videos(limit=60):
    """Videos you opened lately (VLC, Windows) and the newest in Downloads / Videos / Desktop."""
    seen, out = set(), []
    for path in _vlc_recent() + _windows_recent() + [v[0] for v in list_videos()]:
        key = str(path).lower()
        if key in seen or path.suffix.lower() not in EXTENSIONS:
            continue
        seen.add(key)
        if path.is_file():
            out.append(path)
        if len(out) >= limit:
            break
    return out


def recent_photos(limit=60):
    """The newest pictures in Pictures, Desktop and Downloads."""
    roots = folders(("Pictures", "Imagens", "Desktop", "Ambiente de Trabalho", "Downloads", "Transferências"))
    return [v[0] for v in list_videos(PICTURE_EXTENSIONS, roots)[:limit]]


def probe_photo(path):
    """VideoInfo for a picture shown around you: 360° if twice as wide as tall, 3D if its halves match."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        image.draft("L", (2048, 2048))
        gray = np.asarray(image.convert("L"), dtype=np.float32)
    tags = recognize.from_tags(path.stem)
    stereo = tags.stereo or recognize.stereo_of(recognize.stereo_scores(gray))
    layout = tags.merged(recognize.from_picture(gray, stereo))
    h, w = gray.shape
    projection = layout.projection or ("equi" if 1.9 < w / h < 2.1 else "screen")
    return VideoInfo(str(path), path.stem, projection, stereo, source="photo", video_url=str(path), fov=layout.fov)
