"""The AI model files: where they are, getting them (once), and running them.

All models are public, from their makers' official pages: CLIP (OpenAI, ONNX export by Xenova), Depth Anything V2
small (ONNX by onnx-community), YuNet + SFace (OpenCV), the GeoNames list
of cities, and for the assistant Phi-3.5 mini (Microsoft) and Whisper base (OpenAI, CTranslate2 build by Systran). They are data files - nothing in them runs as a program. ONNX models run with ONNX Runtime on the
processor, with half its cores: measured on this PC, the graphics card is busy drawing VR at 90 fps and the AI
was slower there (depth 173 ms vs 58 ms) - and VR stays smooth.
"""
import os
import threading
import urllib.request
import zipfile
from pathlib import Path

from ..core.log import log_error

FOLDER = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PSVRScreen" / "models"
_HF = "https://huggingface.co"
FILES = {   # name -> (address, approximate MB)
    "clip_vision.onnx": (f"{_HF}/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model.onnx", 352),
    "clip_text.onnx": (f"{_HF}/Xenova/clip-vit-base-patch32/resolve/main/onnx/text_model.onnx", 254),
    "clip_tokenizer.json": (f"{_HF}/Xenova/clip-vit-base-patch32/resolve/main/tokenizer.json", 2),
    "depth.onnx": (f"{_HF}/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx", 99),
    "yunet.onnx": (f"{_HF}/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx", 1),
    "sface.onnx": (f"{_HF}/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx", 39),
    "cities15000.zip": ("https://download.geonames.org/export/dump/cities15000.zip", 3),
    "countryInfo.txt": ("https://download.geonames.org/export/dump/countryInfo.txt", 1),
}
# The assistant: Phi-3.5 mini (Microsoft's own ONNX build for processors, MIT licence) - its brain (ai/brain.py)
_PHI = f"{_HF}/microsoft/Phi-3.5-mini-instruct-onnx/resolve/main/cpu_and_mobile/cpu-int4-awq-block-128-acc-level-4"
_PHI_MODEL = "phi-3.5-mini-instruct-cpu-int4-awq-block-128-acc-level-4.onnx"
for _name, _mb in (("genai_config.json", 1), ("config.json", 1), ("special_tokens_map.json", 1), ("tokenizer.json", 2),
                   ("tokenizer_config.json", 1), (_PHI_MODEL, 52), (_PHI_MODEL + ".data", 2728)):
    FILES[f"brain-phi35/{_name}"] = (f"{_PHI}/{_name}", _mb)
# ...and its ears: Whisper base (OpenAI, MIT licence; CTranslate2 build by Systran) - many languages (ai/voice.py)
for _name, _mb in (("config.json", 1), ("model.bin", 145), ("tokenizer.json", 3), ("vocabulary.txt", 1)):
    FILES[f"whisper-base/{_name}"] = (f"{_HF}/Systran/faster-whisper-base/resolve/main/{_name}", _mb)
UNZIPPED = {"cities15000.zip": "cities15000.txt"}
GROUPS = {   # what each feature needs
    "clip": ("clip_vision.onnx", "clip_text.onnx", "clip_tokenizer.json"),
    "depth": ("depth.onnx",),
    "faces": ("yunet.onnx", "sface.onnx"),
    "places": ("cities15000.zip", "countryInfo.txt"),
    "brain": tuple(n for n in FILES if n.startswith("brain-phi35/")),
    "whisper": tuple(n for n in FILES if n.startswith("whisper-base/")),
}

AI_THREADS = max(2, min(6, (os.cpu_count() or 4) // 2))   # half the cores at most: VR keeps the rest
_lock = threading.Lock()
_sessions = {}
status = {}                # group -> "ready" / "downloading 40%" / "failed" (shown in VR)


def path(name):
    return FOLDER / UNZIPPED.get(name, name)


def ready(group):
    return all(path(n).exists() for n in GROUPS[group])


def ensure(group):
    """Download what `group` needs if it isn't here yet (blocking - call from a background thread). True when
    ready."""
    if ready(group):
        status[group] = "ready"
        return True
    FOLDER.mkdir(parents=True, exist_ok=True)
    try:
        names = [n for n in GROUPS[group] if not path(n).exists()]
        total = sum(FILES[n][1] for n in names) or 1
        done = 0
        for name in names:
            url, mb = FILES[name]
            part = FOLDER / (name + ".part")
            part.parent.mkdir(parents=True, exist_ok=True)

            def progress(blocks, block_size, size, done=done, mb=mb):
                if size > 0:
                    status[group] = f"downloading {int(100 * (done + mb * min(1, blocks * block_size / size)) / total)}%"

            urllib.request.urlretrieve(url, part, progress)
            if name in UNZIPPED:
                with zipfile.ZipFile(part) as z:
                    z.extractall(FOLDER)
                part.replace(FOLDER / name)
            else:
                part.replace(FOLDER / name)
            done += mb
        status[group] = "ready"
        return True
    except Exception:
        log_error(f"downloading AI models ({group})")
        status[group] = "failed"
        return False


def session(name):
    """An ONNX Runtime session for a model file (made once), on the processor with AI_THREADS threads."""
    with _lock:
        if name not in _sessions:
            import onnxruntime as ort
            ort.set_default_logger_severity(3)
            options = ort.SessionOptions()
            options.intra_op_num_threads = AI_THREADS
            options.inter_op_num_threads = 1
            _sessions[name] = ort.InferenceSession(str(path(name)), options, providers=["CPUExecutionProvider"])
        return _sessions[name]
