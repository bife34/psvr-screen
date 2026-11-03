"""What the player needs to know about a VR video, wherever it comes from, and looking at its frames."""
from dataclasses import dataclass, field

import numpy as np

from . import recognize

LOOK_AT = (0.2, 0.5)             # the frames looked at, as part of the video's length (the start is often a title)


@dataclass
class VideoInfo:
    key: str                 # identifies the video (YouTube id, page address...)
    title: str
    projection: str          # "equi" (360°), "equi180", "mesh" (YouTube's cube layout), "fisheye" or "screen"
    stereo: str              # "mono", "top_bottom" or "side_by_side"
    source: str = "stream"   # "stream": the player plays video_url / audio_url; "window": the browser window's
                             # picture is shown (the browser plays it, with its sound)
    video_url: str = None
    audio_url: str = None    # None: no sound stream, or the sound is in video_url (then it is the same)
    headers: dict = field(default_factory=dict)   # what the site's server wants to see (Referer, User-Agent)
    duration: float = 0.0    # seconds (0 = unknown, e.g. live)
    fov: float = recognize.DEFAULT_FISHEYE_FOV   # fisheye: degrees the lens sees
    start: float = 0.0       # seconds: where playing starts (Continue watching)
    album: tuple = ()        # a picture: the pictures Previous / Next go through (paths)
    opener: object = None    # a file in a locked room's safe: makes a new decrypting file object (no url)


def open_options(headers):
    """PyAV / ffmpeg options for a site's stream: reconnects and the site's headers."""
    options = {"reconnect": "1", "reconnect_streamed": "1", "reconnect_on_network_error": "1",
               "reconnect_delay_max": "5", "rw_timeout": "15000000"}
    headers = dict(headers or {})
    agent = headers.pop("User-Agent", None)
    if agent:
        options["user_agent"] = agent
    if headers:
        options["headers"] = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return options


def look_at_frames(url, duration, headers=None):
    """The layout the picture shows (recognize.from_picture), over a couple of frames. Raises when the
    stream can't be opened."""
    import av
    scores, first = [], None
    container = av.open(url, options=open_options(headers), timeout=20)
    try:
        stream = container.streams.video[0]
        for part in LOOK_AT if duration > 20 else (0,):
            if part:
                container.seek(int(duration * part * 1_000_000))
            gray = recognize.frame_gray(next(container.decode(stream)))
            scores.append(recognize.stereo_scores(gray))
            first = gray if first is None else first
    finally:
        container.close()
    return recognize.from_picture(first, recognize.stereo_of(np.mean(scores, axis=0)))
