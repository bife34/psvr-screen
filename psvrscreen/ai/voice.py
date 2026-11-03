"""Listening for spoken commands - offline, from the headset's microphone if there is one, else Windows' default
microphone. Press to talk: the microphone is only open after `listen()`, until you stop talking (or press again,
or LISTEN_SECONDS), then it closes again and Whisper (base, many languages) writes down what was said.
`on_text(words)` gets the sentence; `heard` is a line for VR while it listens / understands. Nothing is recorded
or kept."""
import queue
import threading
import time

import numpy as np

from ..core.log import log_error
from . import models

RATE = 16000                       # what Whisper hears
PREFERRED = ("ps vr", "psvr")      # the headset's microphone (its processor box) if it's there
LISTEN_SECONDS = 12.0              # the longest one press listens
WAIT_SECONDS = 6.0                 # ...when nothing is said at all
QUIET_AFTER = 1.0                  # s of quiet after talking: done
NOISE_SECONDS = 0.3                # the first moment measures the room's own noise
TALK_CHUNKS = 5                    # loud 50 ms pieces before it counts as talking
BLOCKED_SECONDS = 0.8              # only zeros this long: Windows isn't letting the sound through


def _microphone():
    import sounddevice as sd
    try:
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and any(p in d["name"].lower() for p in PREFERRED):
                return i
    except Exception:
        pass
    return None


class VoiceListener(threading.Thread):
    def __init__(self, on_text):
        super().__init__(daemon=True, name="voice")
        self.on_text = on_text
        self.heard = ""                 # "" / "Understanding..." (shown in VR)
        self.state = "starting"         # "ready" (microphone closed), "listening", "downloading", "no microphone", "failed"
        self.language = None            # what the last sentence was in ("en", "pt"...)
        self.blocked = False            # the microphone gives pure silence: Windows is asking for permission
                                        # (on the PC screen) or said no (Settings > Privacy > Microphone)
        self._stop = threading.Event()
        self._press = threading.Event()
        self._audio = queue.Queue(maxsize=400)

    def stop(self):
        self._stop.set()

    def listen(self):
        """Open the microphone for one sentence (any thread). Pressing again while listening: that's it."""
        if self.state == "listening":
            self._press.clear()
            self.state = "ready"
        else:
            self._press.set()

    @property
    def listening(self):
        return self.state == "listening"

    def run(self):
        try:
            if not models.ready("whisper"):
                self.state = "downloading"
                if not models.ensure("whisper"):
                    self.state = "failed"
                    return
            import sounddevice as sd
            from faster_whisper import WhisperModel
            model = WhisperModel(str(models.path("whisper-base")), device="cpu", compute_type="int8",
                                 cpu_threads=models.AI_THREADS)
            self.state = "ready"
            while not self._stop.is_set():
                if not self._press.wait(0.3):
                    continue
                self._press.clear()
                sound = self._record(sd)
                if sound is None or len(sound) < RATE * 0.3:
                    continue
                self.heard = "Understanding..."
                try:
                    segments, info = model.transcribe(sound, beam_size=1, vad_filter=False,
                                                      condition_on_previous_text=False)
                    text = " ".join(s.text.strip() for s in segments).strip()
                    self.language = info.language
                finally:
                    self.heard = ""
                if text and not _only_noise(text):
                    self.on_text(text)
        except Exception:
            log_error("voice")
            self.state = "no microphone" if self.state == "starting" else "failed"

    def _record(self, sd):
        """What you say after the press, at 16 kHz (None: no microphone). Ends after a moment of quiet."""
        def feed(data, frames, when, status):
            try:
                self._audio.put_nowait(np.frombuffer(bytes(data), np.int16).copy())
            except queue.Full:
                pass

        stream, rate = None, RATE
        for device in (_microphone(), None):     # the headset's microphone, else Windows' default one
            try:
                rate = int(sd.query_devices(device, "input")["default_samplerate"])
                stream = sd.RawInputStream(samplerate=rate, blocksize=rate // 20, dtype="int16", channels=1,
                                           device=device, callback=feed)
                break
            except Exception:
                continue
        if stream is None:
            self.state = "no microphone"
            return None
        while not self._audio.empty():
            self._audio.get_nowait()
        chunks, noise, talked, quiet_since, loud = [], [], False, None, 0
        start = time.monotonic()
        with stream:      # closed again when this ends: the microphone is off between presses
            self.state, self.blocked = "listening", False
            while self.state == "listening" and not self._stop.is_set():
                now = time.monotonic()
                if now - start > LISTEN_SECONDS or (not talked and now - start > WAIT_SECONDS):
                    break
                try:
                    chunk = self._audio.get(timeout=0.2)
                except queue.Empty:
                    continue
                chunks.append(chunk)
                if now - start > BLOCKED_SECONDS:      # a real microphone is never exactly silent
                    self.blocked = not any(c.any() for c in chunks)
                level = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                if now - start < NOISE_SECONDS:
                    noise.append(level)
                    continue
                floor = max(np.median(noise) if noise else 0.0, 60.0)
                if level > floor * 3:
                    loud += 1                     # a click or a bump isn't talking: a quarter of a second is
                    talked, quiet_since = talked or loud >= TALK_CHUNKS, None
                elif talked:
                    quiet_since = quiet_since or now
                    if now - quiet_since > QUIET_AFTER:
                        break
        if self.state == "listening":
            self.state = "ready"
        if chunks and any(c.any() for c in chunks):
            self.blocked = False
        if not talked or not chunks:
            return None
        sound = np.concatenate(chunks).astype(np.float32) / 32768.0
        if rate != RATE:   # to Whisper's 16 kHz
            n = int(len(sound) * RATE / rate)
            sound = np.interp(np.linspace(0, len(sound) - 1, n), np.arange(len(sound)), sound).astype(np.float32)
        return sound


def _only_noise(text):
    """Whisper writes these for breathing / silence."""
    t = text.lower().strip(" .!?")
    return t in ("", "you", "thank you", "thanks for watching", "obrigado", "bye")
