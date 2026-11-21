import io
import logging
import os
from pathlib import Path
from threading import Event, Timer
from time import monotonic
import wave

import numpy as np
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices, QAudio

SOUND_LEVEL = 0.002
SILENCE_SECONDS = 3
PCM_FORMATS = {
    QAudioFormat.SampleFormat.UInt8: ("u1", 128, 128),
    QAudioFormat.SampleFormat.Int16: ("<i2", 0, 32768),
    QAudioFormat.SampleFormat.Int32: ("<i4", 0, 2147483648),
    QAudioFormat.SampleFormat.Float: ("<f4", 0, 1),
}


def microphones():
    return {bytes(device.id()).decode(): device for device in QMediaDevices.audioInputs()}


def legacy_microphone(devices, saved_name):
    # Older settings stored the PortAudio label, including its host API.
    for identifier, device in devices.items():
        name = device.description()
        if saved_name == name or (saved_name or "").startswith(name + " (Windows "):
            return identifier
    return None


def fallback_microphone(devices, peaks):
    def rank(identifier):
        name = devices[identifier].description().casefold()
        physical = not any(word in name for word in ("mixer", "stereo mix", "virtual", "loopback"))
        return physical, peaks.get(identifier, 0), "usb pnp audio device" in name

    return max(devices, key=rank, default=None)


def loudest_microphone(peaks):
    winner = max(peaks, key=peaks.get, default=None)
    return winner if winner is not None and peaks[winner] >= SOUND_LEVEL else None


class Recorder:
    def __init__(self, device, keep=True, max_seconds=180, test_audio=None):
        self.frames = []
        self.level = self.peak = 0.0
        self.has_samples = False
        self.last_sound = monotonic()
        self.finished = Event()
        self.test_wav = None
        self.source = None
        if keep and (path := test_audio or os.environ.get("ILKELMA_TEST_AUDIO")):
            self.test_wav = Path(path).read_bytes()
            with wave.open(io.BytesIO(self.test_wav), "rb") as source:
                self.rate = source.getframerate()
                duration = source.getnframes() / self.rate
            if duration <= 0:
                raise ValueError("Test WAV is empty.")
            logging.info("TEST AUDIO MODE: %s (%.2f seconds)", path, duration)
            self.timer = Timer(duration, self.finished.set)
            self.timer.daemon = True
            self.timer.start()
            return

        self.keep = keep
        self.captured = 0
        self.paused = False
        self.pending = b""
        audio_format = device.preferredFormat()
        self.dtype, self.offset, self.scale = PCM_FORMATS[audio_format.sampleFormat()]
        self.frame_bytes = audio_format.bytesPerFrame()
        self.rate = audio_format.sampleRate()
        self.channels = audio_format.channelCount()
        self.max_frames = self.rate * max_seconds
        self.source = QAudioSource(device, audio_format)
        self.stream = self.source.start()
        if self.stream is None or self.source.error() != QAudio.Error.NoError:
            self.close()
            raise OSError("Could not start microphone capture.")
        self.stream.readyRead.connect(self.receive)
        self.receive()

    def receive(self):
        data = self.pending + bytes(self.stream.readAll())
        if self.paused:
            self.pending = b""
            return
        complete = len(data) // self.frame_bytes * self.frame_bytes
        self.pending = data[complete:]
        if complete:
            samples = np.frombuffer(data[:complete], dtype=self.dtype).reshape(-1, self.channels)
            self.accept((samples.astype(np.float32) - self.offset) / self.scale)

    def accept(self, samples):
        # Some microphone arrays only put useful audio on one channel.
        levels = np.sqrt(np.mean(samples ** 2, axis=0))
        channel = int(np.argmax(levels))
        self.level = float(levels[channel])
        self.peak = max(self.peak, self.level)
        self.has_samples |= bool(samples.any())
        if self.level >= SOUND_LEVEL:
            self.last_sound = monotonic()
        if self.keep:
            mono = samples[:self.max_frames - self.captured, channel]
            self.frames.append((np.clip(mono * 32768, -32768, 32767)).astype("<i2"))
            self.captured += len(self.frames[-1])
            if self.captured >= self.max_frames:
                self.finished.set()

    def silent(self, now=None):
        if now is None:
            now = monotonic()
        return self.test_wav is None and now - self.last_sound >= SILENCE_SECONDS

    def close(self):
        if self.test_wav is not None:
            self.timer.cancel()
        elif self.source is not None:
            if self.stream is not None:
                self.stream.readyRead.disconnect(self.receive)
            self.source.stop()
            self.source = self.stream = None

    def wav(self):
        if self.test_wav is not None:
            self.finished.wait()
            self.close()
            return self.test_wav
        if not self.has_samples:
            raise ValueError("No sound was recorded.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setparams((1, 2, self.rate, 0, "NONE", "not compressed"))
            for frame in self.frames:
                wav.writeframesraw(frame.astype("<i2", copy=False).tobytes())
        return output.getvalue()
