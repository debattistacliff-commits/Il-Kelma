from contextlib import contextmanager
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from ilkelma.engine.learned_words import WordStore
from ilkelma.engine.listen import RATE, WINDOW, PlaybackPause, listen, open_loopback, speech_chunk
from ilkelma.engine.stt import Engine
from tools.learned_report import counts


def word(text, probability=0.95, start=0, end=1):
    return SimpleNamespace(word=text, probability=probability, start=start, end=end)


def mocked_chunk(words):
    engine = Engine()

    def decode(*args, **kwargs):
        logging.getLogger("faster_whisper").info("Private transcript")
        yield SimpleNamespace(words=words)

    model = Mock(transcribe=Mock(side_effect=lambda *a, **kw: (decode(), None)))
    engine.load = Mock(return_value=model)
    return engine, model


class ListenTests(unittest.TestCase):
    def test_default_playback_endpoint_and_resampling(self):
        import pyaudiowpatch

        endpoint = dict(index=17, defaultSampleRate=48000, maxInputChannels=2, isLoopbackDevice=True)
        stream = Mock()
        stream.get_read_available.return_value = 4800
        stream.read.return_value = np.full((4800, 2), 0.25, dtype=np.float32).tobytes()
        with patch.object(pyaudiowpatch, "PyAudio") as factory:
            host = factory.return_value.__enter__.return_value
            host.get_default_wasapi_loopback.return_value = endpoint
            host.open.return_value.__enter__.return_value = stream
            with open_loopback() as read:
                first, second = read(), read()
                self.assertAlmostEqual(float(second.mean()), 0.25 * 2 ** 0.5, places=4)
                self.assertEqual(len(second), 1600)
                self.assertGreater(len(first), 0)
            self.assertEqual(host.open.call_args.kwargs["input_device_index"], 17)
            endpoint["isLoopbackDevice"] = False
            host.open.reset_mock()
            with self.assertRaises(RuntimeError), open_loopback():
                pass
            host.open.assert_not_called()

    def test_vad_rejects_silence_and_bounds_the_chunk(self):
        samples = np.ones(WINDOW, dtype=np.float32)
        with patch("faster_whisper.vad.get_speech_timestamps", return_value=[]) as vad:
            self.assertIsNone(speech_chunk(samples))
            vad.return_value = [{"start": 12 * RATE, "end": 14 * RATE}]
            self.assertEqual(len(speech_chunk(samples)), 10 * RATE)
            vad.return_value = [{"start": 0, "end": WINDOW}]
            self.assertEqual(len(speech_chunk(samples)), WINDOW)

    def test_filters_words_and_writes_only_counts(self):
        words = [word("jien", 0.85), word("għandi"), word("ghandi"), word("frobazz"),
                 word("frobazz"), word("Secret", 0.8499), word("Doreen"), word("clifford"),
                 word("@handle"), word("123"), word("x"), word("mela", end=30)]
        engine, model = mocked_chunk(words)
        stop = Event()
        chunks = 0

        @contextmanager
        def reader():
            nonlocal chunks
            chunks += 1
            if chunks > 5:
                stop.set()
            yield lambda: np.ones(WINDOW, dtype=np.float32)

        with TemporaryDirectory(dir="out") as folder, self.assertLogs(level="INFO") as logs:
            path = Path(folder) / "learned_words.sqlite"
            store = WordStore(path)
            listen(engine, store, stop, lambda: True, {"clifford"}, reader=reader, vad=lambda audio: audio)
            self.assertEqual(list(Path(folder).iterdir()), [path])
            self.assertEqual(store.hints(), ("frobazz", "għandi", "jien"))
            self.assertEqual(store.patterns(), {"ghandi": "għandi"})
            self.assertEqual(counts(path)["new_words"], [0, 0])
            self.assertNotIn(b"Private transcript", path.read_bytes())
            self.assertEqual(logs.output, ["INFO:root:Listen and learn: 12 words checked, 4 learned"] * 5)
        self.assertEqual(model.transcribe.call_count, 5)
        self.assertEqual(model.transcribe.call_args.kwargs["language"], "mt")
        self.assertTrue(model.transcribe.call_args.kwargs["word_timestamps"])
        self.assertFalse(model.transcribe.call_args.args[0].any())

    def test_pauses_and_drops_partial_buffer_for_tts_or_recording(self):
        for activity in ("tts", "recording"):
            with self.subTest(activity=activity):
                engine, model = mocked_chunk([word("jien")])
                playback, stop = PlaybackPause(), Event()
                state = object()
                opened = 0
                store = Mock(observe=Mock(return_value=1))

                def read():
                    nonlocal state
                    if opened == 1:
                        if activity == "tts":
                            with playback.speaking():
                                pass
                        else:
                            state = None
                        return np.full(WINDOW // 2, 3, dtype=np.float32)
                    return np.ones(WINDOW, dtype=np.float32)

                @contextmanager
                def reader():
                    nonlocal opened, state
                    opened += 1
                    yield read
                    if opened == 1:
                        state = object()

                observed = []

                def vad(audio):
                    observed.append(float(audio.mean()))
                    return audio

                store.observe.side_effect = lambda *a, **kw: stop.set() or 1
                self.assertEqual(listen(engine, store, stop, lambda: state, (), reader=reader,
                                        vad=vad, playback=playback), "stopped")
                self.assertEqual(observed, [1.0])
                model.transcribe.assert_called_once()

    def test_busy_model_skips_capture_and_queued_foreground_skips_inference(self):
        engine, model = mocked_chunk([])
        reader = Mock()
        time = iter(range(100))
        with engine.lock:
            result = listen(engine, Mock(), Event(), lambda: True, (), reader=reader,
                            clock=lambda: next(time), session_seconds=4)
        self.assertEqual(result, "timeout")
        reader.assert_not_called()
        engine.foreground_jobs = 1
        self.assertIsNone(engine.listen_words(np.ones(WINDOW), lambda: False))
        model.transcribe.assert_not_called()
