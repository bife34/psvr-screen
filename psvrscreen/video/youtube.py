"""YouTube: is a page a VR video (360° / 180° / 3D), and where are its picture and sound streams.

Uses yt-dlp. YouTube marks spherical formats in the format note: "2160s" (s = spherical), "equi" (the usual
360° picture, equirectangular) or "mesh" (YouTube's cube layout, see render/video_sphere.py). Many "VR"
videos are uploaded as normal flat videos holding two pictures side by side (made for phone VR viewers):
those are recognised by their title and the picture (recognize.py) and shown on a screen or a dome.
"""
import re

from . import recognize
from .info import VideoInfo, look_at_frames

WATCH_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:\S*&)?v=|shorts/|live/)|youtu\.be/)([\w-]{11})")
MAX_PIXELS = 3840 * 2160 * 1.1      # picture size the player decodes smoothly (4K, or 2880x2880 3D)
CODEC_PREFERENCE = ("vp9", "vp09", "avc1", "av01")   # VP9 decodes fastest here; AV1 is the slowest


def video_id(url):
    """The YouTube video id in a page address, or None."""
    m = WATCH_URL.search(url or "")
    return m.group(1) if m else None


def _projection(fmt):
    note = (fmt.get("format_note") or "").lower()
    if "mesh" in note:
        return "mesh"
    if "equi" in note or re.search(r"\d+s(\d+)?\b", note):
        return "equi"
    return None


def _decodable(fmt):
    """8-bit picture formats over plain https (10-bit HDR VP9.2 / AV1 would need converting every frame)."""
    codec = (fmt.get("vcodec") or "none").lower()
    if codec == "none" or fmt.get("protocol") != "https" or (fmt.get("acodec") or "none") != "none":
        return False
    if codec.startswith("vp9.2") or codec.startswith("vp09.02"):
        return False
    if codec.startswith("av01") and codec.split(".")[3:4] != ["08"]:   # av01.<profile>.<level>.<bit depth>...
        return False
    return codec.startswith(CODEC_PREFERENCE)


def _codec_rank(fmt):
    codec = (fmt.get("vcodec") or "").lower()
    return -next((i for i, c in enumerate(CODEC_PREFERENCE) if codec.startswith(c)), len(CODEC_PREFERENCE))


def probe(vid):
    """VideoInfo for a YouTube video id if it is a VR video, else None. Slow (a few seconds): call it from a
    background thread. Raises on network / YouTube errors."""
    import yt_dlp   # slow to import: only when needed
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info("https://www.youtube.com/watch?v=" + vid, download=False)
    title = info.get("title") or "Video"
    formats = info.get("formats") or []
    candidates = [f for f in formats if _decodable(f) and _projection(f)]
    if not candidates:
        if not recognize.looks_vr(title):
            return None
        candidates = [f for f in formats if _decodable(f)]   # a flat video made for VR viewers
        if not candidates:
            return None
    fitting = [f for f in candidates if (f.get("width") or 0) * (f.get("height") or 0) <= MAX_PIXELS] or \
        [min(candidates, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))]
    video = max(fitting, key=lambda f: ((f.get("height") or 0) * (f.get("width") or 0), _codec_rank(f),
                                        f.get("fps") or 0))
    sounds = [f for f in formats if (f.get("vcodec") or "none") == "none" and (f.get("acodec") or "none") != "none"
              and f.get("protocol") == "https"]
    audio = max(sounds, key=lambda f: f.get("abr") or 0) if sounds else None
    duration = float(info.get("duration") or 0)
    marked = _projection(video)                      # what YouTube says beats everything
    tags = recognize.from_tags(title)
    try:
        picture = look_at_frames(video["url"], duration)
    except Exception:
        picture = recognize.Layout()
    # The picture decides 3D and fisheye; flat 3D uploads with square halves are usually made for phone
    # viewers (a screen), not VR180 - that needs YouTube's mark or a "180" in the title.
    layout = tags.merged(recognize.Layout("fisheye" if picture.projection == "fisheye" else None, picture.stereo))
    stereo = layout.stereo or "mono"
    projection = marked or layout.projection or ("screen" if stereo != "mono" else None)
    if projection is None:
        return None                                  # "VR" in the title, but just a normal video
    if projection == "equi" and stereo == "side_by_side" and marked != "mesh":
        projection = "equi180"                       # VR180: two half-spheres side by side
    return VideoInfo(vid, title, projection, stereo, video_url=video["url"], audio_url=audio["url"] if audio else None,
                     duration=duration, fov=layout.fov)
