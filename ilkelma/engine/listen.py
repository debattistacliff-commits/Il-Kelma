"""Learn word counts from Windows playback without saving recordings."""
from contextlib import contextmanager
import logging
import re
from threading import Lock
from time import monotonic
import unicodedata

import numpy as np

from .learned_words import WordStore, tokens

RATE = 16000
WINDOW = 15 * RATE
SESSION_SECONDS = 60 * 60


class PlaybackPause:
    def __init__(self):
        self.lock = Lock()
        self.active = self.revision = 0
        self.resume_at = 0

    @contextmanager
    def speaking(self):
        with self.lock:
            self.active += 1
            self.revision += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                # Leave time for audio already queued to the output device.
                self.resume_at = monotonic() + 0.25

    def snapshot(self):
        with self.lock:
            if not self.active and monotonic() >= self.resume_at:
                return self.revision
        return None


playback_pause = PlaybackPause()


@contextmanager
def open_loopback():
    import av
    import pyaudiowpatch as audio

    with audio.PyAudio() as host:
        device = host.get_default_wasapi_loopback()
        if not device["isLoopbackDevice"]:
            raise RuntimeError("The default output has no loopback device")
        rate = int(device["defaultSampleRate"])
        channels = device["maxInputChannels"]
        layout = av.AudioLayout(f"{channels}c").name
        size = rate // 10
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
        with host.open(format=audio.paFloat32, channels=channels, rate=rate, input=True,
                       input_device_index=device["index"], frames_per_buffer=size) as stream:
            def read():
                if stream.get_read_available() < size:
                    return None
                data = stream.read(size, exception_on_overflow=False)
                frame = av.AudioFrame.from_ndarray(
                    np.frombuffer(data, dtype=np.float32).reshape(1, -1), format="flt", layout=layout,
                )
                frame.sample_rate = rate
                parts = resampler.resample(frame)
                if parts:
                    return np.concatenate([part.to_ndarray()[0] for part in parts])
                return None

            yield read


def speech_chunk(samples):
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    spans = get_speech_timestamps(samples, VadOptions(min_speech_duration_ms=250))
    if not spans:
        return None
    start, end = spans[0]["start"], spans[-1]["end"]
    start = min(start, len(samples) - 10 * RATE)
    return samples[start:max(end, start + 10 * RATE)]


def eligible_words(words, duration):
    for word in words:
        if not (word.probability >= 0.85 and 0 <= word.start < word.end <= duration + 0.02):
            continue
        raw = unicodedata.normalize("NFC", word.word).strip()
        # Check the entire Whisper token so stripping punctuation cannot expose a handle.
        raw = raw.strip('.,!?;:"“”‘’«»()')
        if re.search(r"[\d@#_/\\]", raw):
            continue
        if any(start == 0 and end == len(raw) for _, _, _, start, end in tokens(raw)):
            yield raw


def listen(engine, store, stop, foreground, excluded, *,
           reader=open_loopback, vad=speech_chunk, playback=playback_pause,
           clock=monotonic, session_seconds=SESSION_SECONDS):
    """Run one session. The stream is closed while paused and during recognition."""
    deadline = clock() + session_seconds

    def done():
        return stop.is_set() or clock() >= deadline

    def snapshot():
        voice = playback.snapshot()
        state = foreground()
        if not done() and voice is not None and state is not None:
            return voice, state
        return None

    while not done():
        stamp = snapshot()
        if stamp is None or engine.lock.locked():
            stop.wait(0.05)
            continue

        def cancelled():
            return snapshot() != stamp

        samples = np.empty(WINDOW, dtype=np.float32)
        filled = 0
        try:
            with reader() as read:
                while not cancelled() and not engine.lock.locked() and filled < WINDOW:
                    block = read()
                    if block is None:
                        stop.wait(0.02)
                        continue
                    count = min(len(block), WINDOW - filled)
                    samples[filled:filled + count] = block[:count]
                    filled += count
                    block = None
            if filled < WINDOW or cancelled() or engine.lock.locked():
                continue
            speech = vad(samples)
            if speech is None or cancelled():
                continue
            words = engine.listen_words(speech, cancelled)
            if words is not None and not cancelled():
                selected = " | ".join(eligible_words(words, len(speech) / RATE))
                learned = store.observe(selected, language="mt", excluded=excluded) or 0
                logging.info("Listen and learn: %d words checked, %d learned", len(words), learned)
        finally:
            samples.fill(0)
            speech = words = selected = None

    return "stopped" if stop.is_set() else "timeout"
