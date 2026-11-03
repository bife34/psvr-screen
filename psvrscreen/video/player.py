"""Plays a video: picture and sound from separate streams (as YouTube serves them), kept in sync.

After a seek the picture starts at the keyframe just before the spot (decoding up to the exact second takes
seconds at 4K) and the sound starts where the picture does.

One thread decodes the picture into a short queue of frames, one decodes the sound into a buffer the sound
card plays. The sound card's progress is the clock; the render thread asks `frame()` for the picture that is
due now. Without sound, the wall clock runs the video. Seeking restarts both threads at the new spot.

Frames are YUV 4:2:0 planes (Y full size, U and V half size): the GPU turns them into colors.
"""
import collections
import threading
import time

import av

from .info import open_options

AUDIO_RATE = 48000
VIDEO_QUEUE = 8                    # decoded frames waiting (~0.15-0.3 s)
AUDIO_BUFFER_SECONDS = 1.5


class Frame:
    def __init__(self, time_s, frame):
        if frame.format.name != "yuv420p":
            frame = frame.reformat(format="yuv420p")
        w, h = frame.width, frame.height
        self.time, self.width, self.height = time_s, w, h
        planes = frame.to_ndarray()                 # (h * 3/2, w): Y, then U and V (each h/4 rows of w)
        self.y = planes[:h]
        self.u = planes[h:h + h // 4].reshape(h // 2, w // 2)
        self.v = planes[h + h // 4:h + h // 2].reshape(h // 2, w // 2)


class VideoPlayer:
    def __init__(self, video_url, audio_url=None, duration=0.0, start=0.0, headers=None):
        self.duration = duration
        self._options = open_options(headers)
        self.error = None
        self.ended = False
        self._urls = (video_url, audio_url)
        self._lock = threading.Condition()
        self._frames = collections.deque()
        self._audio = collections.deque()        # float32 (n, 2) chunks, in order
        self._audio_samples = 0
        self._position = start                   # seconds of sound played (the clock, before latency)
        self._target = start                     # where the last seek went
        self._generation = 0                     # +1 per seek: threads drop what they decoded before
        self._started = -1                       # generation whose start (first picture) is known
        self._paused = False
        self.volume = 1.0                        # 1 = as recorded
        self._wall = (time.monotonic(), start)   # clock without sound: (when, position then)
        self._video_done = self._audio_done = False
        self._stop = False
        self._stream = None
        self._latency = 0.0
        if audio_url:
            try:
                import sounddevice
                self._stream = sounddevice.OutputStream(samplerate=AUDIO_RATE, channels=2, dtype="float32",
                                                        callback=self._play_sound)
                self._latency = self._stream.latency
            except Exception:
                self._stream = None
        self._threads = [threading.Thread(target=self._run, args=(self._decode_video,), daemon=True,
                                          name="video-picture")]
        if self._stream is not None:
            self._threads.append(threading.Thread(target=self._run, args=(self._decode_audio,), daemon=True,
                                                  name="video-sound"))
        for t in self._threads:
            t.start()
        if self._stream is not None:
            self._stream.start()

    # -- control (any thread) -----------------------------------------------------------------------
    @property
    def paused(self):
        return self._paused

    def set_paused(self, paused):
        with self._lock:
            if paused != self._paused:
                self._wall = (time.monotonic(), self._clock())
                self._paused = paused

    def seek(self, seconds):
        if self.duration:
            seconds = min(seconds, max(0.0, self.duration - 1.0))
        seconds = max(0.0, seconds)
        with self._lock:
            self._generation += 1
            self._target = self._position = seconds
            self._wall = (time.monotonic(), seconds)
            self._frames.clear()
            self._audio.clear()
            self._audio_samples = 0
            self._video_done = self._audio_done = False
            self.ended = False
            self._lock.notify_all()

    @property
    def position(self):
        with self._lock:
            return self._clock()

    @property
    def buffering(self):
        """Waiting for the network (nothing to show / play yet)."""
        with self._lock:
            return not self._paused and not self._frames and not self._video_done

    def close(self):
        """Stops at once; the decoding threads end by themselves (one may be waiting for the network)."""
        self._stop = True
        with self._lock:
            self._lock.notify_all()
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass

    # -- the picture for the render thread ----------------------------------------------------------
    def frame(self):
        """The frame due now (each frame once), or None if the one shown is still right."""
        with self._lock:
            clock = self._clock()
            while len(self._frames) >= 2 and self._frames[1].time <= clock:
                self._frames.popleft()               # late: skip it
            if self._frames and self._frames[0].time <= clock + 0.01:
                f = self._frames.popleft()
                self._lock.notify_all()
                return f
            if not self._frames and self._video_done and (self._audio_done or self._stream is None):
                self.ended = True
            return None

    # -- inside --------------------------------------------------------------------------------------
    def _clock(self):
        if self._stream is not None and not self._audio_done:
            return max(self._target, self._position - self._latency)
        when, pos = self._wall
        return pos if self._paused else pos + time.monotonic() - when

    def _run(self, decode):
        try:
            decode()
        except Exception as e:
            if not self._stop:
                self.error = str(e).splitlines()[0][:120] if str(e) else type(e).__name__

    def _wait_room(self, generation, full):
        """Block while the queue is full (or paused); False if a seek / close happened meanwhile."""
        with self._lock:
            while not self._stop and generation == self._generation and full():
                self._lock.wait(0.1)
            return not self._stop and generation == self._generation

    def _loop(self, url, open_stream, handle, leader):
        """Decode `url` from the current seek target on; `handle(frame, target, generation)` per frame.
        Seeks restart it at the new target. The leader (picture) starts at the keyframe before the target and
        makes that the start; the other one waits for it, then starts there exactly."""
        container = av.open(url() if callable(url) else url, options=self._options, timeout=20)   # (a safe's file)
        try:
            stream = open_stream(container)
            while not self._stop:
                with self._lock:
                    generation = self._generation
                    while not leader and not self._stop and generation == self._generation                             and self._started != generation:
                        self._lock.wait(0.1)
                    if generation != self._generation:
                        continue
                    target = self._target
                if target > 0:
                    container.seek(int(target * 1_000_000), backward=True, any_frame=False)
                finished = True
                for frame in container.decode(stream):
                    if self._stop or generation != self._generation:
                        finished = False
                        break
                    if frame.time is None:
                        continue
                    if leader:
                        target = self._set_start(generation, frame.time)
                    elif frame.time < target - 0.001:
                        continue
                    if not handle(frame, target, generation):
                        finished = False
                        break
                if finished:
                    if leader:
                        self._set_start(generation, target)
                    yield generation        # end of the stream: wait for a seek (or close)
                    with self._lock:
                        while not self._stop and generation == self._generation:
                            self._lock.wait(0.2)
        finally:
            container.close()

    def _set_start(self, generation, first):
        """The picture's first frame after a seek: playback starts there. Returns the start."""
        with self._lock:
            if generation == self._generation and self._started != generation:
                self._started = generation
                if self._target - 10 < first < self._target:
                    self._target = self._position = first
                    self._wall = (time.monotonic(), first)
                self._lock.notify_all()
            return self._target

    def _decode_video(self):
        def open_stream(container):
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            return stream

        def handle(frame, target, generation):
            f = Frame(frame.time, frame)
            if not self._wait_room(generation, lambda: len(self._frames) >= VIDEO_QUEUE):
                return False
            with self._lock:
                self._frames.append(f)
            return True

        for generation in self._loop(self._urls[0], open_stream, handle, leader=True):
            with self._lock:
                if generation == self._generation:
                    self._video_done = True

    def _decode_audio(self):
        resampler = av.AudioResampler(format="flt", layout="stereo", rate=AUDIO_RATE)

        def handle(frame, target, generation):
            start = frame.time
            for out in resampler.resample(frame):
                samples = out.to_ndarray().reshape(-1, 2)
                if start < target:                       # starts before the seek target: cut the front
                    samples = samples[int((target - start) * AUDIO_RATE):]
                if not len(samples):
                    continue
                if not self._wait_room(generation, lambda: self._audio_samples > AUDIO_BUFFER_SECONDS * AUDIO_RATE):
                    return False
                with self._lock:
                    self._audio.append(samples)
                    self._audio_samples += len(samples)
            return True

        for generation in self._loop(self._urls[1], lambda c: c.streams.audio[0], handle, leader=False):
            with self._lock:
                if generation == self._generation:   # the video runs on without sound from here
                    self._wall = (time.monotonic(), self._clock())
                    self._audio_done = True

    def _play_sound(self, out, count, _time, _status):
        """Sound card callback: the next `count` samples (silence while paused or waiting for the picture)."""
        out.fill(0)
        with self._lock:
            if self._paused or (not self._frames and not self._video_done):
                return
            filled = 0
            while filled < count and self._audio:
                chunk = self._audio[0]
                n = min(len(chunk), count - filled)
                out[filled:filled + n] = chunk[:n] * self.volume
                if n == len(chunk):
                    self._audio.popleft()
                else:
                    self._audio[0] = chunk[n:]
                filled += n
            self._audio_samples -= filled
            self._position += filled / AUDIO_RATE
            if filled:
                self._lock.notify_all()
