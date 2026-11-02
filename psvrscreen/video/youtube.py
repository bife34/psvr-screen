"""YouTube: is a page a VR video (360° / 180° / 3D), and where are its picture and sound streams.

Uses yt-dlp. YouTube marks spherical formats in the format note: "2160s" (s = spherical), "equi" (the usual
360° picture, equirectangular) or "mesh" (YouTube's cube layout, see render/video_sphere.py). Many "VR"
videos are uploaded as normal flat videos holding two pictures side by side (made for phone VR viewers):
those are recognised by their title and shown on a big screen ("screen").

Stereo 3D isn't marked anywhere: one frame is looked at - if its left and right halves (or top and bottom)
are nearly the same picture, it is 3D. Everything can be changed in the player.
"""
import re
from dataclasses import dataclass

import numpy as np

WATCH_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:\S*&)?v=|shorts/|live/)|youtu\.be/)([\w-]{11})")
MAX_PIXELS = 3840 * 2160 * 1.1      # picture size the player decodes smoothly (4K, or 2880x2880 3D)
CODEC_PREFERENCE = ("vp9", "vp09", "avc1", "av01")   # VP9 decodes fastest here; AV1 is the slowest
VR_TITLE = re.compile(r"\b(vr|3d|sbs|180|360|stereoscopic)\b", re.IGNORECASE)
SAME_PICTURE = 0.8               # halves correlating this much hold the same picture (3D)


@dataclass
class VideoInfo:
    video_id: str
    title: str
    video_url: str
    audio_url: str           # None if the video has no separate sound stream
    projection: str          # "equi" (360°), "equi180" (180°), "mesh" (YouTube's cube layout) or "screen"
    stereo: str              # "mono", "top_bottom" or "side_by_side"
    width: int
    height: int
    duration: float          # seconds (0 = unknown, e.g. live)


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
    if codec.startswith("av01") and not codec.endswith(".08"):
        return False
    return codec.startswith(CODEC_PREFERENCE)


def _codec_rank(fmt):
    codec = (fmt.get("vcodec") or "").lower()
    return -next((i for i, c in enumerate(CODEC_PREFERENCE) if codec.startswith(c)), len(CODEC_PREFERENCE))


def guess_stereo(projection, width, height):
    """Without a frame to look at: two pictures stacked make a picture twice as tall as one."""
    aspect = height / max(width, 1)
    single = {"equi": 0.5, "mesh": 2 / 3}.get(projection)   # 360° picture 2:1, cube layout 3:2
    return "top_bottom" if single and aspect > single * 1.5 else "mono"


def _similar(a, b):
    a, b = a - a.mean(), b - b.mean()
    norm = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / norm) if norm else 0.0


def stereo_of(y):
    """"side_by_side", "top_bottom" or "mono" from a frame's brightness plane (2D array)."""
    h, w = y.shape
    small = y[::max(1, h // 128), ::max(1, w // 256)].astype(np.float32)
    h, w = small.shape
    if _similar(small[:, :w // 2], small[:, w // 2:w // 2 * 2]) > SAME_PICTURE:
        return "side_by_side"
    if _similar(small[:h // 2], small[h // 2:h // 2 * 2]) > SAME_PICTURE:
        return "top_bottom"
    return "mono"


def _look_at_frame(url, duration):
    """Stereo layout from a frame a bit into the video (the start is often a black title)."""
    import av
    container = av.open(url, timeout=20)
    try:
        if duration > 20:
            container.seek(int(min(duration * 0.2, 120) * 1_000_000))
        frame = next(container.decode(container.streams.video[0]))
        return stereo_of(frame.to_ndarray(format="gray"))
    finally:
        container.close()


def probe(vid):
    """VideoInfo for a YouTube video id if it is a VR video, else None. Slow (a few seconds): call it from a
    background thread. Raises on network / YouTube errors."""
    import yt_dlp   # slow to import: only when needed
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info("https://www.youtube.com/watch?v=" + vid, download=False)
    formats = info.get("formats") or []
    candidates = [f for f in formats if _decodable(f) and _projection(f)]
    if not candidates:
        if not VR_TITLE.search(info.get("title") or ""):
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
    projection = _projection(video) or "screen"
    w, h = video.get("width") or 0, video.get("height") or 0
    duration = float(info.get("duration") or 0)
    try:
        stereo = _look_at_frame(video["url"], duration)
    except Exception:
        stereo = guess_stereo(projection, w, h)
    if projection == "screen" and stereo == "mono":
        return None                      # "VR" in the title, but just a normal video
    if projection == "equi" and stereo == "side_by_side":
        projection = "equi180"           # VR180: two half-spheres side by side
    return VideoInfo(vid, info.get("title") or "Video", video["url"], audio["url"] if audio else None,
                     projection, stereo, w, h, duration)
