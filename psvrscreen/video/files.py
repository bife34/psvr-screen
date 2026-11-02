"""VR videos saved on the PC: find them, and what kind each one is (recognize.py: name tags, then picture).

Downloading is the usual way to watch videos from sites that don't let other players stream them.
"""
import os
import time
from pathlib import Path

from . import recognize
from .info import VideoInfo, look_at_frames

EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
SEARCH_DEPTH = 2                 # folders inside the searched folders that are looked into
MAX_FILES = 300


def folders():
    """Where downloaded videos usually are: Downloads, Videos, Desktop (also the OneDrive ones)."""
    home = Path.home()
    found = []
    for base in (home, home / "OneDrive"):
        for name in ("Downloads", "Videos", "Desktop", "Transferências", "Vídeos", "Ambiente de Trabalho"):
            path = base / name
            if path.is_dir() and path.resolve() not in [p.resolve() for p in found]:
                found.append(path)
    return found


def list_videos():
    """[(path, size in bytes, modified time)] of the video files found, newest first."""
    videos = []
    for root in folders():
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - root_depth >= SEARCH_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
            for name in filenames:
                if Path(name).suffix.lower() in EXTENSIONS:
                    path = Path(dirpath) / name
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    videos.append((path, stat.st_size, stat.st_mtime))
    videos.sort(key=lambda v: -v[2])
    return videos[:MAX_FILES]


def describe(path, size, modified):
    """One line for the list: name · size · how old, and what the name says about it."""
    tags = recognize.from_tags(path.stem)
    kind = " ".join(x for x in ({"equi": "360°", "equi180": "180°", "fisheye": "Fisheye"}.get(tags.projection),
                                {"side_by_side": "3D ↔", "top_bottom": "3D ↕"}.get(tags.stereo)) if x)
    days = (time.time() - modified) / 86400
    age = "today" if days < 1 else f"{int(days)} d ago"
    return f"{path.stem}   ·   {size / 1e9:.1f} GB   ·   {age}" + (f"   ·   {kind}" if kind else "")


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
