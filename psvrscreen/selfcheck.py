"""`PSVR Screen.exe --check` (or `python -m psvrscreen --check`): loads every part of the app and every library it
uses later on (AI, video, voice...), writes what's missing to selfcheck.txt in the app's data folder and ends.
Touches nothing (no monitors, no headset, no windows) - safe while the app runs. For finding why an installed
copy fails ("No module named ..."), on any PC.
"""
import importlib
import pkgutil
import sys
import time
import traceback

# Libraries loaded only when a feature is used (inside functions): an installed copy can lack them unnoticed
LATER = ["numpy", "PIL.Image", "cv2", "av", "sounddevice", "yt_dlp", "uiautomation", "comtypes", "cryptography",
         "cryptography.hazmat.primitives.ciphers.aead", "cryptography.hazmat.primitives.kdf.scrypt",
         "onnxruntime", "onnxruntime_genai", "faster_whisper", "ctranslate2", "tokenizers", "hid", "usb.core",
         "libusb_package", "glfw", "OpenGL.GL", "openvr", "mss", "windows_capture", "winrt.windows.media.control",
         "winrt.windows.foundation", "tkinter", "sqlite3", "unittest", "urllib.request", "zipfile", "hashlib",
         "numpy.testing", "unicodedata"]


def _probes():
    """Each native part used once, harmlessly (a missing DLL / data file only shows when it's used)."""
    def sound():
        import sounddevice
        return f"{len(sounddevice.query_devices())} sound devices"

    def usb():
        import libusb_package
        import usb.core
        backend = libusb_package.get_libusb1_backend()
        return f"libusb ok, {len(list(usb.core.find(find_all=True, backend=backend)))} USB devices"

    def hid_list():
        import hid
        return f"{len(hid.enumerate())} HID devices"

    def video():
        import io
        import av
        import numpy as np
        buf = io.BytesIO()
        with av.open(buf, "w", format="mp4") as out:
            stream = out.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            for _ in range(3):
                frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode():
                out.mux(packet)
        buf.seek(0)
        with av.open(buf) as inp:
            frames = sum(1 for _ in inp.decode(video=0))
        return f"video encode + decode ok ({frames} frames)"

    def ai():
        import onnxruntime
        from .ai import models
        have = [n for n in ("depth.onnx", "clip_vision.onnx") if models.path(n).exists()]
        if have:
            models.session(have[0])
        return f"onnxruntime {onnxruntime.__version__}" + (f", {have[0]} loads" if have else " (no model downloaded yet)")

    def opengl():
        import glfw
        if not glfw.init():
            raise RuntimeError("glfw.init failed")
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        window = glfw.create_window(16, 16, "check", None, None)
        glfw.make_context_current(window)
        from OpenGL import GL
        version = GL.glGetString(GL.GL_VERSION).decode()
        glfw.destroy_window(window)
        glfw.terminate()
        return f"OpenGL {version}"

    def screen_reader():
        import uiautomation
        return f"uiautomation ok ({uiautomation.GetRootControl().ClassName})"

    def youtube():
        import yt_dlp
        yt_dlp.YoutubeDL({"quiet": True})
        return f"yt-dlp {yt_dlp.version.__version__}"

    def capture():
        import windows_capture
        return f"windows_capture {getattr(windows_capture, '__version__', 'ok')}"

    def media():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as manager
        return f"media controls ok ({manager.__name__})"

    def vault():
        from .core import cryptomator
        return f"cryptomator module ok ({cryptomator.__name__})"

    return [("sound", sound), ("usb", usb), ("hid", hid_list), ("video", video), ("ai", ai), ("opengl", opengl),
            ("screen reader", screen_reader), ("youtube", youtube), ("window capture", capture),
            ("media", media), ("vault", vault)]


def run():
    from .core.paths import DATA_DIR
    lines, failed = [], 0
    for name, probe in _probes():
        try:
            lines.append(f"ok       [{name}] {probe()}")
        except Exception as e:
            failed += 1
            lines.append(f"FAILED   [{name}] {type(e).__name__}: {str(e)[:200]}")
    names = ["psvrscreen"] + [m.name for m in pkgutil.walk_packages(sys.modules["psvrscreen"].__path__,
                                                                      "psvrscreen.")]
    for name in names + LATER:
        if name == "psvrscreen.__main__":
            continue
        try:
            importlib.import_module(name)
            lines.append(f"ok       {name}")
        except Exception as e:
            failed += 1
            last = traceback.format_exc().strip().splitlines()[-1]
            lines.append(f"MISSING  {name}: {type(e).__name__}: {e}  ({last[:160]})")
    head = (f"PSVR Screen self-check {time.strftime('%Y-%m-%d %H:%M:%S')} - "
            f"{'everything loads' if not failed else f'{failed} problem(s)'}\n"
            f"python {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}  exe={sys.executable}\n")
    report = head + "\n".join(lines) + "\n"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "selfcheck.txt").write_text(report, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(report, flush=True)
        except Exception:
            pass
    return 1 if failed else 0
