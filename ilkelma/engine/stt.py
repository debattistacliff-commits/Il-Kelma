"""Local speech recognition and language routing."""
import gc
import io
from inspect import signature
import logging
import os
from pathlib import Path
import sys
from contextlib import contextmanager
from threading import Lock, get_ident

from ilkelma.languages import chosen, packs
from .learned_words import WordStore
from .maltese_fix import fix
from .tidy import collapse_repeats, punctuate, tidy

PARAKEET_MODEL = "nemo-parakeet-tdt-0.6b-v3"
PARAKEET_FILES = ("config.json", "decoder_joint-model.int8.onnx", "encoder-model.int8.onnx", "vocab.txt")

ROOT = Path(__file__).resolve().parents[2]
_DLLS = []


def enable_cuda():
    library = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for folder in ("cublas", "cudnn"):
        path = library / folder / "bin"
        if path.is_dir():
            _DLLS.append(os.add_dll_directory(str(path)))
            os.environ["PATH"] = str(path) + os.pathsep + os.environ["PATH"]


def segment_text(segment):
    text = segment.text.rstrip()
    if text.endswith("-") and segment.words:
        # A cut-off word after a pause can be a false start at the end of the clip.
        for previous, current in reversed(list(zip(segment.words, segment.words[1:]))):
            if current.start - previous.end >= 0.5 and segment.end - current.start <= 1:
                return "".join(word.word for word in segment.words if word.start < current.start)
    return text


def choose_language(catalog, probs, detected, text=""):
    """Pick only among the installed language packs (English, Maltese, Italian).

    Whisper often labels Maltese as Arabic, Turkish or even English, so we add each pack's
    aliases to its score and give a boost when the words look like that pack's language."""
    scores = {}
    for code, pack in catalog.items():
        scores[code] = probs.get(code, 0.0) + sum(probs.get(alias, 0.0) for alias in pack.get("auto_aliases", []))
    if not probs:
        if detected in catalog:
            return detected
        return next((code for code, pack in catalog.items() if detected in pack.get("auto_aliases", [])), "en" if "en" in catalog else "mt")
    words = set(text.lower().replace(",", " ").replace(".", " ").replace("?", " ").split())
    for code, pack in catalog.items():
        common = set(pack.get("common_words", "").split())
        if common and len(words & common) >= 2:
            scores[code] += 0.5
    return max(scores, key=scores.get)


class Engine:
    def __init__(self, device="cuda", backend="whisper"):
        self.device = device
        self.backend = backend
        self.parakeet = None
        self.lock = Lock()
        self.priority_lock = Lock()
        self.foreground_jobs = 0
        self.loaded = None
        self.model = None

    def load(self, repository):
        if repository == self.loaded:
            return self.model
        from faster_whisper import WhisperModel

        self.model = None
        self.loaded = None
        gc.collect()
        if self.device == "cuda" and not _DLLS:
            enable_cuda()
        folder = ROOT / "models" / repository.rsplit("/", 1)[-1]
        if not all((folder / name).is_file() for name in ("model.bin", "tokenizer.json")):
            raise RuntimeError(f"Model missing. Run download_models.py: {folder.name}")
        self.model = WhisperModel(str(folder), device=self.device, local_files_only=True,
                                  compute_type="int8_float16" if self.device == "cuda" else "int8")
        self.loaded = repository
        return self.model

    def raw(self, audio, repository, language=None):
        model = self.load(repository)
        options = {
            "condition_on_previous_text": False,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 350, "speech_pad_ms": 150},
            "no_speech_threshold": 0.6,
            "log_prob_threshold": -1.0,
            "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            "beam_size": 5,
        }
        parameters = signature(model.transcribe).parameters
        if language == "mt" and "hotwords" in parameters:
            hints = WordStore().hints()
            if hints:
                options["hotwords"] = " ".join(hints)
        if "hallucination_silence_threshold" in parameters:
            options.update(word_timestamps=True, hallucination_silence_threshold=0.75)
        segments, info = model.transcribe(io.BytesIO(audio), language=language, **options)
        self.last_language_probs = dict(getattr(info, "all_language_probs", None) or [])
        segments = list(segments)
        while segments and (segments[-1].avg_logprob < -1.2 or segments[-1].no_speech_prob > 0.8):
            segments.pop()
        if not segments:
            return "", info.language
        text = [segment.text for segment in segments[:-1]]
        text.append(segment_text(segments[-1]))
        return tidy(" ".join(text)), info.language

    def recognize_parakeet(self, audio):
        from faster_whisper.audio import decode_audio

        if self.parakeet is None:
            import onnx_asr

            folder = ROOT / "models" / "parakeet-tdt-0.6b-v3"
            if not all((folder / name).is_file() for name in PARAKEET_FILES):
                raise RuntimeError("Parakeet model missing. Run download_models.py --parakeet.")
            self.parakeet = onnx_asr.load_model(PARAKEET_MODEL, folder, quantization="int8",
                                               providers=["CPUExecutionProvider"])
        samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
        return tidy(self.parakeet.recognize(samples, sample_rate=16000))

    @contextmanager
    def foreground(self):
        with self.priority_lock:
            self.foreground_jobs += 1
        try:
            with self.lock:
                yield
        finally:
            with self.priority_lock:
                self.foreground_jobs -= 1

    def listen_words(self, samples, cancelled):
        """Use idle model time for one playback chunk and return only timed words."""
        with self.priority_lock:
            if self.foreground_jobs or cancelled() or not self.lock.acquire(blocking=False):
                return None
        thread = get_ident()
        logger = logging.getLogger("faster_whisper")

        def foreground_only(record):
            return record.thread != thread

        logger.addFilter(foreground_only)
        try:
            if cancelled() or self.foreground_jobs:
                return None
            model = self.load(packs()["mt"]["model"])
            if cancelled() or self.foreground_jobs:
                return None
            segments, _ = model.transcribe(
                samples, language="mt", word_timestamps=True, condition_on_previous_text=False,
                beam_size=1, temperature=0.0, no_speech_threshold=0.6,
            )
            words = []
            try:
                while not cancelled() and not self.foreground_jobs:
                    segment = next(segments, None)
                    if cancelled() or self.foreground_jobs:
                        return None
                    if segment is None:
                        return words
                    words.extend(segment.words or ())
            finally:
                segments.close()
        finally:
            logger.removeFilter(foreground_only)
            self.lock.release()

    def transcribe_idea(self, audio):
        # Use the multilingual model without forcing the output language onto the recording.
        catalog = chosen()
        repository = packs()["en"]["model"]
        with self.foreground():
            text, detected = self.raw(audio, repository)
            language = choose_language(catalog, self.last_language_probs, detected, text)
            if text and detected not in catalog and language == "mt":
                text, _ = self.raw(audio, packs()["mt"]["model"], "mt")
        return {"text": collapse_repeats(text), "language": language}

    def transcribe(self, audio, language=None):
        catalog = chosen()
        with self.foreground():
            backend = self.backend
            if language is None:
                text, language = self.raw(audio, packs()["en"]["model"])
                detected = language
                language = choose_language(catalog, getattr(self, "last_language_probs", {}), detected, text)
                if backend == "parakeet":
                    text = self.recognize_parakeet(audio)
                elif language != detected or packs()[language]["model"] != packs()["en"]["model"]:
                    text, _ = self.raw(audio, packs()[language]["model"], language)
            elif backend == "parakeet":
                text = self.recognize_parakeet(audio)
            else:
                text, _ = self.raw(audio, packs()[language]["model"], language)
        text = collapse_repeats(text)
        if packs()[language]["fixer"]:
            text = fix(text, lang=language)
        return {"text": punctuate(text, language), "language": language}

    def fix(self, text):
        return fix(text)

    def speak(self, text, language=None):
        from .tts import audio_bytes

        return audio_bytes(text, language)
