"""Places, offline: a GPS position -> the nearest town ("Lisbon, Portugal"), from GeoNames' list of the world's
towns with 15000+ people. Also the other way: which town or country names appear in a question."""
import threading

import numpy as np

from . import models

_lock = threading.Lock()
_data = None


def _load():
    global _data
    with _lock:
        if _data is not None:
            return _data
        countries = {}
        with open(models.path("countryInfo.txt"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) > 4:
                    countries[parts[0]] = parts[4]
        names, coords, country, people = [], [], [], []
        with open(models.path("cities15000.zip"), encoding="utf-8") as f:
            for line in f:
                parts = line.split("\t")
                if parts[7] == "PPLX":                # a part of a town (a district): its town names it
                    continue
                names.append(parts[1])                # the usual name ("Lisbon")
                coords.append((float(parts[4]), float(parts[5])))
                country.append(countries.get(parts[8], parts[8]))
                people.append(int(parts[14] or 0))
        lat, lon = np.radians(np.array(coords, np.float64)).T
        xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)
        _data = (names, country, xyz.astype(np.float32), np.array(people))
        return _data


def name_of(lat, lon):
    """The biggest town within ~15 km (Porto, not one of its parishes), else the nearest within ~60 km, with its
    country; None if farther from any."""
    names, country, xyz, people = _load()
    la, lo = np.radians(lat), np.radians(lon)
    p = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], np.float32)
    closeness = xyz @ p
    near = np.nonzero(closeness > np.cos(15 / 6371))[0]
    if len(near):
        best = int(near[np.argmax(people[near])])
    else:
        best = int(np.argmax(closeness))
        if float(closeness[best]) < np.cos(60 / 6371):
            return None
    return f"{names[best]}, {country[best]}"


def find_in(question):
    """A place named in a question after in / at / from / near / to ("when was I last in Lisbon"): ("town",
    name, (lat, lon)) - the biggest town of that name - or ("country", name, None); None if there isn't one."""
    import re
    names, country, xyz, people = _load()
    q = question.lower()
    spots = [m.end() for m in re.finditer(r"\b(?:in|at|from|near|to|visited|visit)\s+", q)]
    if not spots:
        return None
    countries = {c.lower(): c for c in set(country)}
    best = None
    for start in spots:
        words = re.findall(r"[\w'.-]+", q[start:])[:4]
        for n in range(len(words), 0, -1):          # the longest name first ("new york" before "new")
            name = " ".join(words[:n])
            if name in countries:
                return ("country", countries[name], None)
            if best is None:
                best = name if name in _town_index() else None
        if best:
            i = _town_index()[best]
            lat = float(np.degrees(np.arcsin(xyz[i][2])))
            lon = float(np.degrees(np.arctan2(xyz[i][1], xyz[i][0])))
            return ("town", names[i], (lat, lon))
    return None


_towns = None


def _town_index():
    """Lower-case town name -> its biggest town's index."""
    global _towns
    if _towns is None:
        names, _, _, people = _load()
        _towns = {}
        for i, n in enumerate(names):
            key = n.lower()
            if key not in _towns or people[i] > people[_towns[key]]:
                _towns[key] = i
    return _towns


def km_between(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return float(2 * 6371 * np.arcsin(np.sqrt(h)))
