"""Spoken commands (ai/voice.py hears them): what each sentence does. Render thread.

    find / search / show me ... , when was I ..., how many ...   ask the photo wall (opens Pictures if no wall)
    next / previous                                               next picture / page
    bigger / smaller / closer / farther                           the wall's pictures, or the video
    sphere / ring / flat / galaxy                                 the wall's shape
    show videos / show photos / show everything / clear           what the wall shows
    people, shuffle, recenter, save room                          the wall
    play / pause / louder / quieter / mute / unmute               the video
    three d / step inside / living / flat / make it my room       a photo around you
    timeline                                                      the video playing, as a timeline in space
    exit / close / stop / go back                                 leave the video / timeline / wall
    open photos / videos / files / games / home                   apps
"""
import re
from pathlib import Path

ASK = re.compile(r"^(find|search( for)?|show me|look for|where|when|how many|photos? of|pictures? of|"
                 r"videos? of)\b")
# First words of the commands: only what starts like one shows up in VR while you speak
STARTS = {"find", "search", "show", "look", "where", "when", "how", "photos", "photo", "pictures", "picture",
          "videos", "video", "exit", "close", "stop", "go", "back", "quit", "next", "previous", "last", "three",
          "3d", "step", "inside", "flat", "two", "2d", "normal", "living", "make", "pause", "play", "resume",
          "continue", "louder", "quieter", "softer", "volume", "more", "less", "mute", "unmute", "sound", "forward",
          "skip", "rewind", "timeline", "bigger", "smaller", "zoom", "larger", "closer", "come", "nearer", "farther",
          "further", "move", "only", "just", "everything", "clear", "reset", "all", "people", "faces", "who",
          "shuffle", "mix", "save", "remember", "live", "person", "recenter", "center", "re", "open", "home",
          "sphere", "ring", "tower", "wall", "galaxy", "memory", "space", "switch", "depth", "alive", "set", "ai"}


def _words(text):
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def sounds_like_command(partial):
    words = _words(partial).split()
    return bool(words) and words[0] in STARTS


def handle(shell, text, catch_all=True):
    """Does what the sentence says; returns what happened (shown in VR), or "" if it wasn't a command.
    catch_all: on the photo wall anything else is a search (off when the assistant's brain is there to ask)."""
    t = _words(text)
    wall, video, timeline = shell.wall, shell.video, shell.timeline
    s = shell.settings()

    def is_(*options):
        return t in options

    # Leaving
    if is_("exit", "close", "stop", "go back", "back", "close it", "exit video", "quit"):
        if video is not None:
            shell.stop_video()
            return "Closed"
        if timeline is not None:
            shell.stop_timeline()
            return "Closed the timeline"
        if wall is not None:
            shell.stop_wall()
            return "Closed the photo wall"
        return ""

    # A photo around you
    if video is not None and video.photo:
        if is_("next", "next one", "next picture", "next photo"):
            video.step_photo(1)
            return "Next"
        if is_("previous", "previous one", "last one", "back one", "previous picture", "previous photo"):
            video.step_photo(-1)
            return "Previous"
        if is_("three d", "3d", "depth", "make it three d", "make it 3d"):
            video.set_photo_depth("3d")
            return "3D"
        if is_("step inside", "go inside", "inside", "step in"):
            video.set_photo_depth("inside")
            return "Step inside"
        if is_("flat", "two d", "2d", "normal"):
            video.set_photo_depth("off")
            return "Flat"
        if is_("living", "make it live", "alive", "living photo"):
            video.toggle_living()
            return "Living " + ("on" if shell.settings().photo_living else "off")
        if is_("make it my room", "make it the room", "set as room", "make room"):
            video.make_room()
            return "Making it your room..."

    # A video
    if video is not None and not video.photo:
        if is_("pause", "stop playing", "play", "resume", "continue"):
            video.play_pause()
            return "Pause / play"
        if is_("louder", "volume up", "more volume"):
            video.set_value("video_volume", min(1.5, s.video_volume + 0.15))
            return f"Volume {round(shell.settings().video_volume * 100)}%"
        if is_("quieter", "softer", "volume down", "less volume"):
            video.set_value("video_volume", max(0.0, s.video_volume - 0.15))
            return f"Volume {round(shell.settings().video_volume * 100)}%"
        if is_("mute", "unmute", "sound off", "sound on"):
            video.toggle_mute()
            return "Mute / unmute"
        if is_("forward", "skip", "skip forward"):
            video.skip(10)
            return "+10 s"
        if is_("rewind", "skip back", "go back ten"):
            video.skip(-10)
            return "-10 s"
        if is_("timeline", "show timeline", "timeline in space") and Path(video.info.video_url or "").exists():
            path = Path(video.info.video_url)
            shell.stop_video()
            shell.open_timeline(path)
            return "Timeline in space"
        if is_("closer", "come closer", "nearer", "farther", "further", "farther away", "much farther", "far away"):
            far = {"closer": 1 / 1.3, "come closer": 1 / 1.3, "nearer": 1 / 1.3, "much farther": 3.0,
                   "far away": 3.0}.get(t, 1.3)
            video.set_value("video_distance", s.video_distance * far)
            return "Closer" if far < 1 else "Farther"
        if is_("bigger", "zoom in", "larger"):
            key = "video_screen_degrees" if video.projection == "screen" else "video_size"
            video.set_value(key, getattr(s, key) * 1.15)
            return "Bigger"
        if is_("smaller", "zoom out"):
            key = "video_screen_degrees" if video.projection == "screen" else "video_size"
            video.set_value(key, getattr(s, key) / 1.15)
            return "Smaller"
        if is_("three d", "3d", "make it three d", "make it 3d", "ai three d"):
            video.set_value("video_ai_depth", not s.video_ai_depth)
            return "AI 3D " + ("on" if shell.settings().video_ai_depth else "off")

    # The photo wall
    if wall is not None and video is None:
        shapes = {"sphere": "sphere", "ring": "ring", "tower": "tower", "column": "column", "vertical": "column", "flat": "flat", "flat wall": "flat",
                  "wall": "flat", "galaxy": "galaxy", "memory galaxy": "galaxy", "space": "galaxy"}
        name = re.sub(r"^(make it a |make it |switch to |go to |show as |show |as )", "", t)
        name = re.sub(r" (mode|view|shape)$", "", name)
        if name in shapes:
            wall.set_shape(shapes[name])
            return name.capitalize()
        if is_("next", "next page"):
            wall.go_page(wall.page + 1)
            return f"Page {wall.page + 1}"
        if is_("previous", "previous page", "back a page"):
            wall.go_page(wall.page - 1)
            return f"Page {wall.page + 1}"
        if is_("bigger", "zoom in", "larger"):
            wall.set_value("wall_size", s.wall_size * 1.25)
            return "Bigger"
        if is_("smaller", "zoom out"):
            wall.set_value("wall_size", s.wall_size / 1.25)
            return "Smaller"
        key = "wall_space" if s.wall_shape == "galaxy" else "wall_distance"   # the galaxy: its whole space
        if is_("closer", "come closer", "nearer"):
            wall.set_value(key, getattr(s, key) / 1.5)
            return "Closer"
        if is_("farther", "further", "further away", "farther away", "move away"):
            wall.set_value(key, getattr(s, key) * 1.5)
            return "Farther"
        if is_("much farther", "far away", "way farther", "much further"):
            wall.set_value(key, getattr(s, key) * 4)
            return "Much farther"
        if is_("show videos", "only videos", "videos only", "just videos"):
            wall.set_value("wall_show", "videos")
            return "Videos"
        if is_("show photos", "only photos", "photos only", "just photos", "show pictures", "only pictures"):
            wall.set_value("wall_show", "photos")
            return "Photos"
        if is_("show everything", "show all", "everything", "clear", "clear search", "reset", "all"):
            wall.set_value("wall_show", "all")
            wall.clear_answer()
            return "Everything"
        if is_("people", "show people", "faces", "who"):
            wall.toggle_people()
            return "People"
        if is_("shuffle", "mix", "mix them"):
            wall.shuffle()
            return "Shuffled"
        if is_("save room", "save this room", "remember this", "save memory room"):
            wall.save_room()
            return "Saved"
        if is_("live", "live wall", "play all", "play everything"):
            wall.set_value("wall_live", not s.wall_live)
            return "Live wall " + ("on" if shell.settings().wall_live else "off")
        m = re.fullmatch(r"(?:show )?person (\w+)", t)
        if m:
            numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
            n = numbers.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else 0)
            if n:
                wall.show_person(n - 1)
                return f"Person {n}"

    if is_("recenter", "center", "re center", "reset view"):
        if video is not None:
            video.recenter_here()
        elif wall is not None:
            wall.recenter_here()
        else:
            shell.dock_action("recenter")
        return "Recentered"

    # Apps
    apps = {"photos": "photos", "pictures": "photos", "videos": "videos", "files": "files", "games": "games"}
    m = re.fullmatch(r"(?:open|show|go to) (?:the |my )?(\w+)(?: app)?", t)
    if m and m.group(1) in apps and video is None:
        shell.open_native(apps[m.group(1)])
        return "Opening " + m.group(1)
    if is_("home", "go home", "open home"):
        shell.dock_action("home")
        return "Home"

    # A search / question: the photo wall. You pressed Talk, so on the wall anything else is a search too
    if ASK.match(t) or (catch_all and wall is not None and video is None and t):
        question = re.sub(r"^(find|search for|search|look for|ask)\s+", "", t)
        if video is not None:
            return ""
        if wall is None:
            shell.watch_wall(_pictures_folder(), question=question)
        else:
            wall.ask(question)
        return f"Looking for \"{question}\""
    return ""


def _pictures_folder():
    from ..video import files
    found = files.folders(("Pictures", "Imagens"))
    return found[0] if found else Path.home()
