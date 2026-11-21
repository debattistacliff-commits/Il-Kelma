from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ilkelma.app.screen_reader import ScreenReader
from ilkelma.app.storage import DEFAULTS
from ilkelma.engine.learned_words import WordStore, is_form
from ilkelma.engine.screen_ocr import Line, apply_patterns, pointed_comment, prepare
from ilkelma.engine.stt import Engine
from maltese_fix import Evidence
from tools.learned_report import counts


class LearnedWordsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "out")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "learned_words.sqlite"
        self.store = WordStore(self.path)

    def reader(self, enabled=True):
        reader = Mock(words=self.store, learned_comments=set(), language="mt")
        reader.window.settings = {"learn_read_words": enabled, "language": "mt"}
        return reader

    def test_only_word_forms_and_counts_reach_database(self):
        reader = self.reader()
        ScreenReader.learn_comment(reader, (
            "Privateauthor Prosit",
            "jien Prosit Doreen doreen Clifford fit27ta1971 @HiddenUser 123 "
            "https://example.com/secret www.example.net nope.test/path "
            "someone@example.org mailto:private@example.org 127.0.0.1/private #tagname "
            "hidden_user UnknownTitle frobazz 😀",
        ))
        with closing(sqlite3.connect(self.path)) as db:
            tables = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            persisted = set()
            for name in tables:
                columns = [row[1] for row in db.execute(f"PRAGMA table_info({name})")]
                self.assertIn(columns, [["word", "count"], ["wrong_word", "right_word", "count"]])
                for *forms, count in db.execute(f"SELECT * FROM {name}"):
                    self.assertTrue(all(is_form(form) for form in forms))
                    self.assertIs(type(count), int)
                    persisted.update(forms)
        self.assertEqual(persisted, {"jien", "frobazz"})

    def test_known_words_and_confident_patterns(self):
        for ending in ("!", "?"):
            self.store.observe("jien ghandi it tifla" + ending)
        self.assertEqual(self.store.hints(), ("jien",))
        self.assertEqual(self.store.patterns(), {"ghandi": "għandi", "ittifla": "it-tifla"})
        self.assertEqual(apply_patterns("Ghandi it tifla, @ghandi.", self.store.patterns()),
                         "Għandi it-tifla, @ghandi.")
        with patch("ilkelma.engine.screen_ocr.WordStore", return_value=self.store):
            self.assertEqual(prepare("jien ghandi it tifla", "mt", lambda text, _: text),
                             ("jien għandi it-tifla", "mt"))

    def test_five_distinct_comments_promote_unknown_word(self):
        reader = self.reader()
        pair = ("One", "jien frobazz frobazz frobazz")
        for _ in range(7):
            ScreenReader.learn_comment(reader, pair)
        self.assertEqual(counts(self.path)["new_words"], [1, 1])
        for number in range(3):
            ScreenReader.learn_comment(reader, ("Two", f"jien frobazz {number}"))
        self.assertEqual(counts(self.path)["new_words"], [1, 4])
        ScreenReader.learn_comment(reader, ("Three", "jien frobazz"))
        self.assertEqual(counts(self.path)["new_words"], [0, 0])
        self.assertIn("frobazz", self.store.hints())
        self.assertEqual(self.store.read("SELECT count FROM vocabulary WHERE word='frobazz'"), [(5,)])

    def test_cleanup_at_thirty_days_preserves_younger_words(self):
        first = date(2026, 1, 1)
        self.store.observe("jien frobazz", today=first)
        self.store.observe("jien zazzble", today=first + timedelta(days=1))
        self.store.cleanup(first + timedelta(days=29))
        self.assertEqual(counts(self.path)["new_words"], [2, 2])
        self.store.cleanup(first + timedelta(days=30))
        self.assertEqual(counts(self.path)["new_words"], [1, 1])
        self.assertEqual(counts(self.path)["vocabulary"], [1, 2])
        self.store.observe("jien frobazz", today=first + timedelta(days=30))
        self.assertEqual(counts(self.path)["new_words"], [2, 2])

    def test_setting_off_and_forget(self):
        self.assertTrue(DEFAULTS["learn_read_words"])
        reader = self.reader(enabled=False)
        ScreenReader.learn_comment(reader, ("One", "jien frobazz"))
        self.store.observe("jien frobazz", enabled=False)
        self.assertEqual(counts(self.path)["vocabulary"], [0, 0])
        self.assertFalse(self.path.exists())
        reader.window.settings["learn_read_words"] = True
        ScreenReader.learn_comment(reader, ("One", "jien ghandi frobazz"))
        self.assertTrue(all(rows for rows, _ in counts(self.path).values()))
        ScreenReader.forget_words(reader)
        self.assertTrue(all(totals == [0, 0] for totals in counts(self.path).values()))
        self.assertEqual(self.store.patterns(), {})
        self.assertEqual(self.store.hints(), ())

    def test_point_requires_message_line_and_chat_layout(self):
        label = Line("Privateauthor", (10, 10, 100, 18))
        message = Line("jien frobazz", (10, 23, 140, 37))
        self.assertEqual(pointed_comment([label, message], (25, 30)),
                         ("Privateauthor", "jien frobazz"))
        self.assertIsNone(pointed_comment([label, message], (25, 13)))
        paragraph = Line("Another line", (10, 43, 140, 57))
        self.assertIsNone(pointed_comment([message, paragraph], (25, 50)))

    def test_whisper_hints_are_maltese_only(self):
        engine = Engine()
        received = []

        def transcribe(_audio, language, hotwords=None, **_options):
            received.append((language, hotwords))
            return [], Mock(language=language, all_language_probs=[])

        with patch.object(engine, "load", return_value=Mock(transcribe=transcribe)), patch(
                "ilkelma.engine.stt.WordStore", return_value=self.store):
            self.store.observe("jien jien")
            for code in ("mt", "en", "it", None):
                engine.raw(b"", "model", code)
        self.assertEqual(received, [("mt", "jien"), ("en", None), ("it", None), (None, None)])

    def test_familiar_form_can_break_a_close_corpus_tie(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE words (word, count, fold)")
            db.executemany("INSERT INTO words VALUES (?, ?, ?)",
                           [("hena", 100, "hena"), ("\u0127ena", 90, "hena")])
            self.assertEqual(Evidence(db).spelling("hena"), "hena")
            self.assertEqual(Evidence(db, preferred={"\u0127ena"}).spelling("hena"), "\u0127ena")
            db.execute("UPDATE words SET count=60 WHERE word=?", ("\u0127ena",))
            self.assertEqual(Evidence(db, preferred={"\u0127ena"}).spelling("hena"), "hena")
