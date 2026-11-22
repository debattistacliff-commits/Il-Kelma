import asyncio
from bisect import bisect_left
import io
import os
import re
from itertools import accumulate
from threading import Lock
from time import monotonic

import edge_tts

from ilkelma.languages import packs
from .language import detect
from .listen import playback_pause


async def synthesize(text, language, rate="+0%", *, timed=False):
    for voice in packs()[language]["voices"]:
        try:
            audio = collect_audio(text, voice, rate, timed=True) if timed else collect_audio(text, voice, rate)
            return await asyncio.wait_for(audio, timeout=30)
        except (OSError, TimeoutError, edge_tts.exceptions.EdgeTTSException):
            continue
    raise RuntimeError("The online voices are unavailable.")


async def collect_audio(text, voice, rate="+0%", *, timed=False):
    options = {"boundary": "WordBoundary"} if timed else {}
    speech = edge_tts.Communicate(text, voice, rate=rate, connect_timeout=8, receive_timeout=12, **options)
    audio = bytearray()
    boundaries = []
    async for chunk in speech.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif timed and chunk["type"] == "WordBoundary":
            boundaries.append(chunk)
    if not audio:
        raise RuntimeError("The voice returned no audio.")
    return (bytes(audio), boundaries) if timed else bytes(audio)


def audio_bytes(text, language=None, *, rate="+0%"):
    return asyncio.run(synthesize(text, language or detect(text), rate))


async def cancellable_audio(text, language, stop, rate="+0%", *, timed=False):
    if stop.is_set():
        return None
    args = (text, language or detect(text), rate)
    task = asyncio.create_task(synthesize(*args, timed=True) if timed else synthesize(*args))
    while not stop.is_set():
        done, _ = await asyncio.wait({task}, timeout=.04)
        if done and not stop.is_set():
            return task.result()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return None


_playback_lock = Lock()


def word_timeline(text, boundaries):
    """Map spoken tokens from the online voice service to character spans, in playback order."""
    cursor = 0
    for event in boundaries:
        token = event["text"].strip()
        if not token:
            continue
        match = re.compile(re.escape(token), re.IGNORECASE).search(text, cursor)
        if match:
            start, end = match.span()
            yield event["offset"] / 10_000_000, start, end
            cursor = end


def estimated_timeline(text):
    elapsed = 0.0
    words = list(re.finditer(r"\b\w+(?:[-'’]\w+)*", text))
    end = 0
    for word in words:
        pause = text[end:word.start()]
        if re.search(r"[.!?\n]", pause):
            elapsed += .19
        elif re.search(r"[,;:]", pause):
            elapsed += .1
        yield elapsed, word.start(), word.end()
        elapsed += .28 + min(len(word[0]), 14) * .034
        end = word.end()


def follow_words(timeline, stop, mark, playing, elapsed):
    words = iter(timeline)
    next_word = next(words, None)
    while not stop.is_set() and playing():
        now = elapsed()
        while next_word and next_word[0] <= now and not stop.is_set():
            _, start, end = next_word
            mark(start, end)
            next_word = next(words, None)
        stop.wait(.012)


def follow_sapi(voice, text, stop, mark):
    import pythoncom

    # SAPI positions are measured in UTF-16 units.
    units = list(accumulate((2 if ord(char) > 0xFFFF else 1 for char in text), initial=0))
    estimates = iter(estimated_timeline(text))
    estimate = next(estimates, None)
    started = monotonic()
    last = None
    while not stop.is_set() and not voice.WaitUntilDone(1):
        try:
            status = voice.Status
            length = status.InputWordLength
            position = status.InputWordPosition
        except (AttributeError, pythoncom.com_error):
            length = 0
        if length:
            span = bisect_left(units, position), bisect_left(units, position + length)
            if span != last and 0 <= span[0] < span[1] <= len(text):
                mark(*span)
                last = span
        elif last is None:
            while estimate and estimate[0] <= monotonic() - started:
                mark(*estimate[1:])
                estimate = next(estimates, None)
        stop.wait(.012)


def play_words(text, language, stop, mark, *, online=False):
    """Play speech with word cues. Only callers that allow cloud speech may opt in."""
    if online:
        try:
            result = asyncio.run(cancellable_audio(text, language, stop, timed=True))
            if result is None:
                return
            audio, boundaries = result
            import pygame

            with _playback_lock, playback_pause.speaking():
                if stop.is_set():
                    return
                pygame.mixer.init()
                try:
                    music = pygame.mixer.music
                    music.load(io.BytesIO(audio), "mp3")
                    music.play()
                    timeline = list(word_timeline(text, boundaries)) or estimated_timeline(text)
                    follow_words(timeline, stop, mark, music.get_busy, lambda: music.get_pos() / 1000)
                finally:
                    pygame.mixer.quit()
            return
        except (OSError, RuntimeError, TimeoutError):
            if stop.is_set():
                return

    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        with _playback_lock, playback_pause.speaking():
            if stop.is_set():
                return
            voice = Dispatch("SAPI.SpVoice")
            primary = int(packs()[language].get("lcid", "0x09"), 16)
            for token in voice.GetVoices():
                locales = token.GetAttribute("Language").split(";")
                if any(int(locale, 16) & 0x3FF == primary for locale in locales if locale):
                    voice.Voice = token
                    break
            voice.Speak(text, 17)  # Async and treat the input as plain text.
            try:
                follow_sapi(voice, text, stop, mark)
            finally:
                if stop.is_set():
                    voice.Speak("", 3)  # Cancel queued speech as well as the current sentence.
    finally:
        pythoncom.CoUninitialize()


def play(text, language=None, stop=None, *, rate="+0%"):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame

    import logging
    import time as _time

    logging.info("Read aloud: %d characters, language=%s: %s", len(text), language, text[:80])
    audio = (audio_bytes(text, language, rate=rate) if stop is None
             else asyncio.run(cancellable_audio(text, language, stop, rate)))
    if audio is None:
        return
    started = _time.monotonic()
    with _playback_lock, playback_pause.speaking():
        if stop is not None and stop.is_set():
            return
        pygame.mixer.init()
        try:
            pygame.mixer.music.load(io.BytesIO(audio), "mp3")
            if stop is not None and stop.is_set():
                return
            pygame.mixer.music.play()
            clock = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                if stop is None:
                    clock.tick(25)
                elif stop.wait(.01):
                    pygame.mixer.music.stop()
                    break
        finally:
            pygame.mixer.quit()
            logging.info("Read aloud finished: %d bytes of audio, %.1f s", len(audio), _time.monotonic() - started)


def announce(message):
    """Speak an app message using the Windows voice, without going online."""
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        with playback_pause.speaking():
            Dispatch("SAPI.SpVoice").Speak(message)
    except Exception:  # a missing/broken Windows voice must never crash a task
        import logging
        logging.warning("Windows voice could not say: %s", message)
    finally:
        pythoncom.CoUninitialize()
