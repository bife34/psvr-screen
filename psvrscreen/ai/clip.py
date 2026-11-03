"""CLIP: pictures and words as vectors that point the same way when they mean the same thing.

`images(pictures)` and `text(words)` give unit vectors (512 numbers); their dot product says how well a picture
fits the words (around 0.2 - 0.35 for a good fit). Used for search by talking and for the memory galaxy (similar
pictures lie close together).
"""
import threading

import numpy as np

from . import models

SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
BATCH = 16

_tokenizer = None
_lock = threading.Lock()
_text_cache = {}


def _prepare(image):
    """PIL image -> (3, 224, 224): shortest side to 224, middle cut out, normalised."""
    from PIL import Image
    image = image.convert("RGB")
    w, h = image.size
    k = SIZE / min(w, h)
    image = image.resize((max(SIZE, round(w * k)), max(SIZE, round(h * k))), Image.BICUBIC)
    w, h = image.size
    x, y = (w - SIZE) // 2, (h - SIZE) // 2
    pixels = np.asarray(image.crop((x, y, x + SIZE, y + SIZE)), np.float32) / 255.0
    return ((pixels - MEAN) / STD).transpose(2, 0, 1)


def _unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)


def images(pictures):
    """[PIL image] -> (n, 512) unit vectors."""
    if not pictures:
        return np.zeros((0, 512), np.float32)
    s = models.session("clip_vision.onnx")
    out = []
    for i in range(0, len(pictures), BATCH):
        batch = np.stack([_prepare(p) for p in pictures[i:i + BATCH]])
        out.append(s.run(["image_embeds"], {"pixel_values": batch})[0])
    return _unit(np.concatenate(out).astype(np.float32))


def text(words):
    """Words -> a 512 unit vector ("a photo of ..." added, which CLIP was trained on)."""
    global _tokenizer
    words = words.strip().lower()
    if words in _text_cache:
        return _text_cache[words]
    with _lock:
        if _tokenizer is None:
            from tokenizers import Tokenizer
            _tokenizer = Tokenizer.from_file(str(models.path("clip_tokenizer.json")))
    ids = _tokenizer.encode(f"a photo of {words}").ids[:77]
    s = models.session("clip_text.onnx")
    v = _unit(s.run(["text_embeds"], {"input_ids": np.array([ids], np.int64)})[0][0].astype(np.float32))
    _text_cache[words] = v
    return v


# Words to name groups of pictures in the galaxy (the best fitting one names a group).
LABELS = ("beach", "sea", "mountains", "snow", "forest", "city", "street", "night", "sunset", "food", "drinks",
          "party", "people", "family", "friends", "selfie", "children", "baby", "dog", "cat", "animals", "birds",
          "flowers", "garden", "car", "motorbike", "airplane", "boat", "house", "room", "kitchen", "office",
          "computer screen", "screenshot", "document", "text", "game", "drawing", "art", "sports", "concert",
          "wedding", "christmas", "birthday", "pool", "river", "lake", "desert", "sky", "clouds", "road trip",
          "church", "monument", "museum", "shop", "school", "work", "music", "fashion", "fitness", "nature")
_label_vectors = None


def label_for(vector):
    """The word that fits a group's average vector best."""
    global _label_vectors
    if _label_vectors is None:
        _label_vectors = np.stack([text(w) for w in LABELS])
    return LABELS[int(np.argmax(_label_vectors @ _unit(vector)))]
