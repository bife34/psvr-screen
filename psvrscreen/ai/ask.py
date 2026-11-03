"""Questions and searches about your pictures, spoken or typed, answered from the index (ai/index.py).

    "beach at sunset"                    -> the pictures that look most like it (CLIP)
    "show me photos from 2024"           -> taken in 2024, newest first
    "when was I last in Lisbon"          -> "Last time in Lisbon: 12 June 2025" + those pictures
    "photos of dogs in Portugal in june" -> place + month + what they show
    "how many videos of the sea"         -> "7 videos"

`answer(question, records)` -> Answer: the paths in order and a sentence. Places come from the pictures' GPS;
a place no picture was taken in is searched by how pictures look ("Paris" -> the Eiffel tower...).
"""
import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
FILLER = {"show", "me", "find", "search", "for", "look", "looking", "photos", "photo", "pictures", "picture", "pics",
          "images", "image", "videos", "video", "clips", "of", "my", "the", "a", "an", "with", "from", "in", "at",
          "on", "when", "was", "were", "i", "we", "did", "last", "first", "time", "how", "many", "much", "where",
          "what", "all", "some", "any", "and", "taken", "that", "this", "year", "please", "is", "are", "there",
          "do", "have", "go", "went", "been", "to", "our", "us", "only", "just", "where's", "which"}
GOOD_FIT = 0.215                 # CLIP: a picture fits the words at least this well...
NEAR_BEST = 0.06                 # ...and not much worse than the best one...
MIN_DROP = 0.008                 # ...cut where the fit drops most (if it drops at least this)
MAX_RESULTS = 200
NEAR_KM = 30                     # "in Lisbon": photos taken this close to it (by GPS)


@dataclass
class Answer:
    paths: list = field(default_factory=list)
    text: str = ""                # one sentence for you ("" = just the results)
    title: str = ""               # short, for the wall's bar ("beach · 2024")
    question: str = ""            # what was asked (a memory room asks it again)


def _date(t):
    return datetime.fromtimestamp(t).strftime("%d %B %Y").lstrip("0")


def answer(question, records):
    """records: {path: Record}. Only places your pictures were taken in count as places."""
    q = question.lower().strip().rstrip("?.!")
    words = re.findall(r"[\w'-]+", q)
    want_videos = any(w in ("video", "videos", "clips") for w in words)
    want_photos = any(w in ("photo", "photos", "picture", "pictures", "pics", "images") for w in words)
    when_last = bool(re.search(r"\bwhen\b.*\blast\b|\blast time\b", q))
    when_first = bool(re.search(r"\bwhen\b.*\bfirst\b|\bfirst time\b", q))
    count = bool(re.search(r"\bhow many\b", q))

    # Dates: a year, "this year" / "last year", a month
    now = datetime.now()
    year = month = None
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", q)
    if m:
        year = int(m.group(1))
    elif "this year" in q:
        year = now.year
    elif "last year" in q:
        year = now.year - 1
    for w in words:
        if w in MONTHS and w not in ("may",) or (w == "may" and year):
            month = MONTHS[w]

    # Places your pictures were taken in (longest names first: "new york" before "york")
    taken_in = {r.place for r in records.values() if r.place}
    place = None
    names = {}
    for full in taken_in:
        town, _, country = full.partition(", ")
        names[town.lower()] = town
        names[country.lower()] = country
    near = None                   # (lat, lon) of a town named in the question: photos within NEAR_KM count
    not_there = ""                # a place no photo was taken at (by GPS): said so, and searched by looks
    for name in sorted(names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", q):
            place = names[name]
            q_rest = re.sub(rf"\b{re.escape(name)}\b", " ", q)
            break
    else:
        q_rest = q
        try:
            from . import places
            found = places.find_in(q)
        except Exception:
            found = None
        if found is not None:
            kind_of, name, spot = found
            q_rest = re.sub(rf"\b{re.escape(name.lower())}\b", " ", q)
            if kind_of == "town" and any(r.gps and places.km_between(r.gps, spot) <= NEAR_KM
                                         for r in records.values()):
                place, near = name, spot
            elif kind_of == "country" and any(r.place.endswith(", " + name) for r in records.values()):
                place = name
            else:
                not_there = name
                q_rest = q_rest + " " + name.lower()    # then: what looks like it

    # What's left says what they show
    rest = [w for w in re.findall(r"[\w'-]+", q_rest) if w not in FILLER and w not in MONTHS
            and not re.fullmatch(r"19[5-9]\d|20[0-4]\d", w)]
    looks = " ".join(rest)

    chosen = list(records.values())
    if want_videos and not want_photos:
        chosen = [r for r in chosen if _is_video(r.path)]
    elif want_photos and not want_videos:
        chosen = [r for r in chosen if not _is_video(r.path)]
    if near is not None:
        from . import places
        chosen = [r for r in chosen if r.gps and places.km_between(r.gps, near) <= NEAR_KM]
    elif place:
        chosen = [r for r in chosen if place in r.place.split(", ")]
    if year:
        chosen = [r for r in chosen if r.date and datetime.fromtimestamp(r.date).year == year]
    if month:
        chosen = [r for r in chosen if r.date and datetime.fromtimestamp(r.date).month == month]

    if looks:
        from . import clip
        with_vectors = [r for r in chosen if r.vector is not None]
        if with_vectors:
            scores = np.stack([r.vector for r in with_vectors]) @ clip.text(looks)
            chosen = [with_vectors[i] for i in fitting(scores)]
        else:
            chosen = []
    else:
        chosen.sort(key=lambda r: -r.date)

    title_parts = [looks, place or "", (calendar.month_name[month] if month else ""), str(year or "")]
    title = " · ".join(p for p in title_parts if p) or question.strip()
    kind = "videos" if want_videos and not want_photos else "photos" if want_photos else "pictures"
    if not_there:
        title = " · ".join(p for p in title_parts if p) or not_there
        text = f"No photos taken in {not_there} (by their GPS)" + (" - these look like it" if chosen else "")
        return Answer([r.path for r in chosen], text, title)
    if not chosen:
        return Answer([], f"Nothing found for \"{question.strip()}\"", title)
    text = ""
    if when_last or when_first:
        dated = sorted((r for r in chosen if r.date), key=lambda r: r.date)
        pick = dated[-1] if when_last else dated[0]
        where = f" in {place}" if place else ""
        text = f"{'Last' if when_last else 'First'} time{where}: {_date(pick.date)}"
        chosen = sorted(chosen, key=lambda r: -r.date if when_last else r.date)
    elif count:
        text = _many(len(chosen), kind) + (f" in {place}" if place else "")
    elif place and not looks:
        dated = [r.date for r in chosen if r.date]
        if dated:
            first, last = _date(min(dated)), _date(max(dated))
            text = f"{_many(len(chosen), kind)} in {place}, " + (first if first == last else f"{first} - {last}")
    return Answer([r.path for r in chosen], text, title)


def fitting(scores):
    """Indices of the pictures that fit, best first: of those near the best, up to the biggest drop in fit
    (real matches score close together, then the rest falls away)."""
    order = np.argsort(-scores)
    best = float(scores[order[0]])
    window = [i for i in order if scores[i] >= max(GOOD_FIT, best - NEAR_BEST)][:MAX_RESULTS]
    if len(window) < 3:
        return window
    drops = [float(scores[a] - scores[b]) for a, b in zip(window, window[1:])]
    cut = int(np.argmax(drops))
    return window[:cut + 1] if drops[cut] >= MIN_DROP else window


def _many(n, kind):
    return f"{n} {kind if n != 1 else kind[:-1]}"


def _is_video(path):
    return path.lower().rsplit(".", 1)[-1] in ("mp4", "mkv", "webm", "mov", "m4v", "avi")
