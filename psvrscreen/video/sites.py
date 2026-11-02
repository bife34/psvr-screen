"""Any website: is the page a VR video, and how to play it.

YouTube has its own rules (youtube.py). Other sites: pages whose title or address talk about VR are read
with yt-dlp (it knows ~1800 sites); the video's layout comes from tags and the picture (recognize.py). If
the site's stream can't be played (the site changed, needs a login, protects its video...), the browser
window itself is shown in VR instead: source "window" - the browser plays it, with its sound.
"""
from . import recognize, youtube
from .info import VideoInfo, look_at_frames

PROTOCOLS = {"https", "http", "m3u8", "m3u8_native", None}   # what the player can open (HLS too)
MAX_HEIGHT = 2880                 # decodes smoothly here (5.7K side-by-side is 5760 x 2880)


def probe(url, title):
    """VideoInfo if the page shows a VR video, else None. Slow: call it from a background thread."""
    vid = youtube.video_id(url)
    if vid:
        return youtube.probe(vid)
    if not (recognize.looks_vr(title) or recognize.looks_vr(url)):
        return None
    tags = recognize.from_tags(title, url)
    try:
        info = _stream(url, title)
    except Exception:
        info = None
    if info is not None:
        return info if info.projection else None
    return VideoInfo(url, _short(title), tags.projection, tags.stereo, source="window", fov=tags.fov)


def _short(title):
    """The page title without " - Site - Google Chrome"."""
    for end in (" - Google Chrome", " - Microsoft Edge", " — Mozilla Firefox", " - Brave"):
        if title.endswith(end):
            title = title[:-len(end)]
    return title


def _height(fmt):
    return fmt.get("height") or 0


def _stream(url, title):
    """The page's video stream via yt-dlp, checked by opening it; None if there is none / it won't open."""
    import yt_dlp   # slow to import: only when needed
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        # process=False: take the site's list as it is (yt-dlp's own pick fails on some sites)
        info = ydl.extract_info(url, download=False, process=False)
        if info.get("_type") not in (None, "video"):
            return None
        formats = [f for f in info.get("formats") or [info] if f.get("url") and f.get("protocol") in PROTOCOLS
                   and (f.get("vcodec") or "") != "none"]
        if not formats:
            return None
        fitting = [f for f in formats if _height(f) <= MAX_HEIGHT] or [min(formats, key=_height)]
        video = max(fitting, key=lambda f: (_height(f), f.get("tbr") or 0))
        try:
            headers = ydl._calc_headers({**info, **video})   # yt-dlp's headers for this format (Referer...)
        except Exception:
            headers = dict(video.get("http_headers") or {})
    if (video.get("acodec") or "") == "none":   # picture only: the best sound-only format
        sounds = [f for f in info.get("formats") or [] if (f.get("vcodec") or "") == "none" and f.get("url")
                  and (f.get("acodec") or "none") != "none" and f.get("protocol") in PROTOCOLS]
        audio = max(sounds, key=lambda f: f.get("abr") or 0)["url"] if sounds else None
    else:
        audio = video["url"]                     # sound in the same file (or unknown: try it)
    duration = float(info.get("duration") or 0)
    picture = look_at_frames(video["url"], duration, headers)   # raises if the stream won't open
    layout = recognize.from_tags(title, url, video["url"]).merged(picture)
    stereo = layout.stereo or "mono"
    projection = layout.projection or ("screen" if stereo != "mono" else None)
    return VideoInfo(url, info.get("title") or _short(title), projection, stereo, video_url=video["url"],
                     audio_url=audio, headers=headers, duration=duration, fov=layout.fov)
