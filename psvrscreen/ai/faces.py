"""Faces: finding them (YuNet) and telling whose they are (SFace: 128 numbers per face; the same person's faces
point the same way). `group(faces)` puts faces of the same person together."""
import threading

import numpy as np

from . import models

DETECT_SIDE = 1280            # pictures are looked at this big at most
MIN_SCORE = 0.85
MIN_FACE = 40                 # px (at DETECT_SIDE): smaller faces are too blurry to tell apart
SAME_PERSON = 0.42            # cosine similarity (SFace's own threshold is 0.363 - a bit stricter here)

_local = threading.local()    # OpenCV's face objects aren't shared between threads


def _tools():
    if not hasattr(_local, "detector"):
        import cv2
        _local.detector = cv2.FaceDetectorYN.create(str(models.path("yunet.onnx")), "", (320, 320), MIN_SCORE)
        _local.recognizer = cv2.FaceRecognizerSF.create(str(models.path("sface.onnx")), "")
    return _local.detector, _local.recognizer


def find(rgb):
    """rgb (h, w, 3) uint8 -> [(box (x, y, w, h) as parts of the picture, feature (128,) unit vector)]."""
    import cv2
    detector, recognizer = _tools()
    h, w = rgb.shape[:2]
    k = min(1.0, DETECT_SIDE / max(h, w))
    if k < 1.0:
        rgb = cv2.resize(rgb, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    detector.setInputSize((w, h))
    _, found = detector.detect(bgr)
    out = []
    for f in found if found is not None else []:
        if f[2] < MIN_FACE or f[3] < MIN_FACE:
            continue
        aligned = recognizer.alignCrop(bgr, f)
        feature = recognizer.feature(aligned)[0].astype(np.float32)
        feature /= max(np.linalg.norm(feature), 1e-8)
        out.append(((float(f[0] / w), float(f[1] / h), float(f[2] / w), float(f[3] / h)), feature))
    return out


def group(faces):
    """faces: [(key, feature)] -> [[key...] per person], biggest group first; one-off faces left out."""
    groups, means = [], []
    for key, feature in faces:
        if means:
            sims = np.stack(means) @ feature
            best = int(np.argmax(sims))
            if sims[best] >= SAME_PERSON:
                groups[best][0].append(key)
                total = groups[best][1] + feature
                groups[best][1] = total
                means[best] = total / max(np.linalg.norm(total), 1e-8)
                continue
        groups.append([[key], feature.copy()])
        means.append(feature)
    people = [g[0] for g in groups if len({k[0] for k in g[0]}) >= 2]   # on two pictures at least
    return sorted(people, key=len, reverse=True)
