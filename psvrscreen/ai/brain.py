"""The assistant's brain: what you said (any language, your own words) -> the app actions that do it.

A small language model, Phi-3.5 mini (Microsoft, MIT licence, 4-bit for the processor, 2.7 GB), runs on this PC
with ONNX Runtime GenAI - nothing leaves the PC. It can only answer with actions from ACTIONS (the answer is held
to that JSON form while it's written), so it can't do anything the app doesn't offer. Its instructions are read
once and kept; each request then takes about 1-2.5 s on the processor (measured: 12 test sentences, 10 right
- the 1.7B Qwen 3 was faster but turned "turn off" into "restart").

    brain = Brain(); brain.start()            # loads in the background (downloads first, once)
    brain.ask("those pictures bigger", "photo wall", on_actions)   # on_actions([{"do": "size", ...}]) later
"""
import json
import queue
import threading
import time

from ..core.log import log_error
from . import models

MODEL = "brain-phi35"


def _act(name, **props):
    p = {"do": {"const": name}}
    p.update(props)
    return {"type": "object", "properties": p, "required": list(p), "additionalProperties": False}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


_TEXT = {"type": "string", "maxLength": 80}
TARGETS = _enum("wall", "video", "window")
ACTIONS = [
    _act("open", app=_enum("photos", "videos", "files", "games", "home", "settings", "apps")),
    _act("open_folder", folder=_TEXT),
    _act("find_photos", query=_TEXT),
    _act("wall_shape", shape=_enum("sphere", "ring", "flat", "tower", "column", "galaxy")),
    _act("size", target=TARGETS, change=_enum("bigger", "smaller", "much_bigger", "much_smaller")),
    _act("distance", target=TARGETS, change=_enum("closer", "farther", "much_closer", "much_farther")),
    _act("player", command=_enum("play", "pause", "next", "previous", "louder", "quieter", "mute", "unmute",
                                 "forward", "rewind")),
    _act("recenter"),
    _act("close", what=_enum("video", "photo_wall", "menu", "window")),
    _act("power", what=_enum("restart", "off")),
    _act("reply", text={"type": "string", "maxLength": 160}),
]
SCHEMA = {"type": "object", "properties": {"actions": {"type": "array", "maxItems": 4, "items": {"anyOf": ACTIONS}}},
          "required": ["actions"], "additionalProperties": False, "x-guidance": {"whitespace_flexible": False}}

INSTRUCTIONS = """You are the voice assistant of PSVR Screen, a VR desktop app. Turn the user's words (any language) \
into the app actions that do exactly that, as JSON. Never add actions the user didn't ask for. "Now showing" tells \
you what is in front of the user: "it", "this", "them", "those" mean that. If no action fits, use "reply" briefly.
Actions:
open: open an app (photos, videos, files, games, home, settings, apps)
open_folder: show a folder (its name, like downloads, desktop, pictures, or a path)
find_photos: search the user's photos (query in English, only what to find)
wall_shape: arrange the photo wall (sphere, ring, flat, tower, column, galaxy)
size / distance: make the wall, video or window bigger / smaller, closer / farther
player: the video playing (play, pause, next, previous, louder, quieter, mute, unmute, forward, rewind)
recenter: put everything in front of the user again
close: close the video, photo wall, menu or window ("go back", "exit", "stop")
power: restart or turn off the whole app
reply: say something (questions, or what can't be done)
Examples:
now showing: photo wall | user: show my cat pictures
{"actions":[{"do":"find_photos","query":"cat"}]}
now showing: photo wall | user: put them in a ring and bring them closer
{"actions":[{"do":"wall_shape","shape":"ring"},{"do":"distance","target":"wall","change":"closer"}]}
now showing: video | user: way too quiet
{"actions":[{"do":"player","command":"louder"}]}
now showing: video | user: make it much bigger
{"actions":[{"do":"size","target":"video","change":"much_bigger"}]}
now showing: video | user: go back
{"actions":[{"do":"close","what":"video"}]}
now showing: photo wall | user: exit
{"actions":[{"do":"close","what":"photo_wall"}]}
now showing: windows | user: fotos da praia em galáxia
{"actions":[{"do":"find_photos","query":"beach"},{"do":"wall_shape","shape":"galaxy"}]}
now showing: windows | user: turn it off
{"actions":[{"do":"power","what":"off"}]}
now showing: windows | user: what can you do
{"actions":[{"do":"reply","text":"I open apps and folders, find your photos, arrange them, control videos and move things."}]}
now showing: windows | user: what's the weather
{"actions":[{"do":"reply","text":"I can't check the weather."}]}"""


class Brain(threading.Thread):
    """Loads the model once, then answers requests one at a time (its own thread)."""

    def __init__(self):
        super().__init__(daemon=True, name="brain")
        self.state = "starting"        # "downloading", "loading", "ready", "failed"
        self._requests = queue.Queue()
        self.busy = False

    def ask(self, words, showing, on_actions):
        """Queue a request: on_actions(list of actions) is called from the brain's thread ([] if it failed)."""
        self._requests.put((words, showing, on_actions))

    def run(self):
        try:
            if not models.ready("brain"):
                self.state = "downloading"
                if not models.ensure("brain"):
                    self.state = "failed"
                    return
            self.state = "loading"
            import onnxruntime_genai as og
            folder = str(models.path(MODEL))
            _use_threads(folder)
            model = og.Model(folder)
            tokenizer = og.Tokenizer(model)
            params = og.GeneratorParams(model)
            params.set_search_options(max_length=2048, do_sample=False)
            params.set_guidance("json_schema", json.dumps(SCHEMA))
            generator = og.Generator(model, params)
            head = tokenizer.encode(f"<|system|>\n{INSTRUCTIONS}<|end|>\n")
            generator.append_tokens(head)         # read once: every request starts from here
            self.state = "ready"
        except Exception:
            log_error("brain")
            self.state = "failed"
            return
        while True:
            words, showing, on_actions = self._requests.get()
            self.busy = True
            actions = []
            try:
                t = time.perf_counter()
                generator.rewind_to(len(head))
                generator.append_tokens(tokenizer.encode(
                    f"<|user|>\nnow showing: {showing} | user: {words}<|end|>\n<|assistant|>\n"))
                stream, out = tokenizer.create_stream(), []
                while not generator.is_done() and len(out) < 300:
                    generator.generate_next_token()
                    out.append(stream.decode(generator.get_next_tokens()[0]))
                actions = json.loads("".join(out)).get("actions", [])
                self.last_seconds = time.perf_counter() - t
            except Exception:
                log_error(f"brain: {words!r}")
            self.busy = False
            try:
                on_actions(actions)
            except Exception:
                log_error("brain actions")


def _use_threads(folder):
    """The model runs on AI_THREADS of the processor's cores (VR keeps the rest)."""
    import os
    path = os.path.join(folder, "genai_config.json")
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    options = config["model"]["decoder"].setdefault("session_options", {})
    if options.get("intra_op_num_threads") != models.AI_THREADS:
        options["intra_op_num_threads"] = models.AI_THREADS
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
