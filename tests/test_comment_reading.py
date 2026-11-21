import asyncio
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ilkelma.app import storage
from ilkelma.app.screen_reader import ScreenReader
from ilkelma.engine.comment_reading import (
    comment_language, comment_speech, readable_comment,
)
from ilkelma.engine.screen_ocr import Line, clean_comment, read_chat
from ilkelma.engine.tts import synthesize


def unchanged(text, language):
    return text


class CommentReadingTests(unittest.TestCase):
    def test_language_counts_words_and_breaks_ties_in_maltese(self):
        for text, expected in (
            ("Kif inti lovely?", "mt"),
            ("hello everyone how are you", "en"),
            ("ciao bella come stai", "it"),
            ("grazzi ħafna, love you", "mt"),
            ("hello ciao", "mt"),
            ("kif you you", "en"),
            ("kif cosa stai", "it"),
            ("tabib hello", "mt"),
            ("frobazz", "mt"),
        ):
            with self.subTest(text=text):
                self.assertEqual(comment_language(text), expected)

    def test_labels_and_voice_modes(self):
        settings = storage.DEFAULTS.copy()
        for language, body, label in (
            ("mt", "Prosit", "tgħid"),
            ("en", "Clear skies", "says"),
            ("it", "Ciao bella", "dice"),
        ):
            for mode in ("auto", "mt", "en"):
                with self.subTest(language=language, mode=mode):
                    settings["comment_language"] = mode
                    actual = comment_speech("Neri", body, language, settings, unchanged)
                    self.assertEqual(actual, (f"Neri {label}: {body}",
                                              language if mode == "auto" else mode, "+0%"))
        self.assertEqual(comment_speech("", "Ciao bella", "it", settings, unchanged)[0],
                         "Ciao bella")
        settings["comment_author"] = False
        self.assertEqual(comment_speech("Neri", "Ciao bella", "it", settings, unchanged)[0],
                         "Ciao bella")

    def test_ocr_cleanup_and_maltese_only_corrections(self):
        self.assertEqual(clean_comment(" 0k, 0K, x0k "), "ok, OK, x0k")
        self.assertEqual(read_chat([Line("0k", (60, 20, 100, 36), 245)]),
                         [(("", "0k"), 36)])
        for raw, expected in (
            ("Sabihaaaaa", "Sabiħa"),
            ("hello  l can see a really loooong sky", "hello  I can see a really loong sky"),
            ("ciao bellaaaa", "ciao bellaa"),
        ):
            with self.subTest(raw=raw):
                corrections = Mock(side_effect=unchanged)
                language = comment_language(raw)
                spoken, _, _ = comment_speech("", raw, language, storage.DEFAULTS, corrections)
                self.assertEqual(spoken, expected)
                self.assertEqual(corrections.call_count, int(language == "mt"))

    def test_greetings_and_emoji_filter(self):
        settings = storage.DEFAULTS.copy()
        self.assertTrue(readable_comment("Hi! 👋", settings))
        for enabled in (False, True):
            settings["comment_skip_greetings"] = enabled
            self.assertFalse(readable_comment("👋 ✨!", settings))
            self.assertTrue(readable_comment("Hi there!", settings))
            self.assertEqual(readable_comment("Hellooo! 👋", settings), not enabled)

    def test_mentions_use_the_configured_name_and_word_boundaries(self):
        settings = storage.DEFAULTS | {"user_name": "Neri"}
        self.assertTrue(readable_comment("Hello everyone", settings))
        settings["comment_mentions_only"] = True
        for text, expected in (
            ("@NERI, are you here?", True), ("Hello Neri!", True),
            ("Hi Nerina", False), ("Hi @neri_fan", False), ("Hello everyone", False),
        ):
            self.assertEqual(readable_comment(text, settings), expected)
        settings["user_name"] = "@Neri Galea"
        self.assertTrue(readable_comment("Ciao Neri  Galea", settings))
        settings["user_name"] = ""
        self.assertFalse(readable_comment("Ciao Neri Galea", settings))

    def test_speed_reaches_synthesis_and_settings_round_trip(self):
        settings = storage.DEFAULTS.copy()
        for speed, rate in (("slow", "-20%"), ("normal", "+0%"), ("fast", "+20%")):
            settings["comment_speed"] = speed
            text, voice, actual_rate = comment_speech("Neri", "Ciao", "it", settings, unchanged)
            self.assertEqual(actual_rate, rate)
            with patch("ilkelma.engine.tts.collect_audio", return_value=b"audio") as collect:
                self.assertEqual(asyncio.run(synthesize(text, voice, actual_rate)), b"audio")
                self.assertEqual(collect.call_args.args[2], rate)
                self.assertTrue(collect.call_args.args[1].startswith("it-IT-"))
        settings.update(comment_language="en", comment_author=False, comment_skip_greetings=True,
                        comment_mentions_only=True, learn_read_words=False)
        with TemporaryDirectory(dir=".") as folder, patch.object(storage, "DATA", Path(folder)):
            storage.save("settings", settings)
            self.assertEqual(storage.load("settings", {}), settings)

    def test_reader_skips_filtered_comments_and_respects_learning_toggle(self):
        settings = storage.DEFAULTS | {"comment_skip_greetings": True, "learn_read_words": False}
        reader = Mock(speech=None, available=True, outlines=[], point_result=None,
                      tracker=Mock(), queue=deque([("Neri", "Hi!"), ("Neri", "Kif inti?")]),
                      point_token=None, learned_comments=set(), language="en")
        reader.window.settings = settings
        reader.point_timer.isActive.return_value = False
        reader.read_comment.side_effect = lambda *args, **kwargs: ScreenReader.read_comment(
            reader, *args, **kwargs
        )
        reader.learn_comment.side_effect = lambda *args: ScreenReader.learn_comment(reader, *args)
        ScreenReader.next_speech(reader)
        reader.speak.assert_called_once_with("Neri tgħid: Kif inti?", "mt", comment=True, rate="+0%")
        reader.words.observe.assert_not_called()
        self.assertFalse(reader.queue)
        settings["learn_read_words"] = True
        ScreenReader.read_comment(reader, ("Neri", "Grazzi ħafna, love you"))
        reader.words.observe.assert_called_once_with(
            "Grazzi ħafna, love you", excluded={"neri"}, fallback="en", language="mt"
        )
