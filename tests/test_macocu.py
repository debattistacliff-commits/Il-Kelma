from contextlib import closing
import gzip
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from build_macocu_words import build as build_words
from build_sentence_bank import ROOT, build
from ilkelma.app import __main__ as app, learning, storage
from ilkelma.engine.sentence_bank import lookup
from maltese_fix import Evidence, match_case


class BankTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.output = self.folder / "sentences.sqlite"
        source = self.folder / "sentences.tsv.gz"
        rows = [
            ("Ejja sal-baħar għada.", "Come to the beach tomorrow.", ".7", "newsbook.com.mt"),
            ("Ejja sal- baħar għada.", "Come to the beach tomorrow.", ".9", "eur-lex.europa.eu"),
            ("Ejjew ħdejn il-baħar għada.", "Come to the beach tomorrow.", ".9", "cnimalta.org"),
            ("Nibqaʼ d-dar.", "I am staying home.", ".9", "example.mt"),
            ("Se nasal għada.", "I will arrive tomorrow.", ".69", "example.mt"),
            ("Wasalt lura.", "I came back.", ".70", "example.mt"),
            ("Dan l-artikolu.", "See Article 12.", ".9", "example.mt"),
            ("Ikteb lil xi ħadd.", "Email name@example.org please.", ".9", "example.mt"),
            ("123 456", "123 456", ".9", "example.mt"),
            ("Iva", "Yes", ".9", "example.mt"),
        ]
        with gzip.open(source, "wt", encoding="utf-8", newline="") as file:
            file.write("src_text\ttrg_text\tbicleaner_ai_score\tdomain_en\n")
            for row in rows:
                file.write("\t".join(row) + "\n")
        self.counts = dict(build(source, self.output))

    def test_filter_normalize_deduplicate_and_rank(self):
        self.assertEqual(self.counts, {"newsbook.com.mt": 1, "cnimalta.org": 1, "example.mt": 2})
        matches = lookup(self.output, "Come to the beach tomorrow", "mt")
        self.assertEqual(matches[0].mt, "Ejja sal-baħar għada.")
        self.assertTrue(matches[1].exact)
        self.assertEqual(lookup(self.output, "Nibqa’ d-dar", "en")[0].mt, "Nibqa' d-dar.")

    def test_changed_meaning_is_only_an_example_and_italian_uses_glosses(self):
        matches = lookup(self.output, "Don't come to the beach tomorrow", "mt")
        self.assertTrue(matches)
        self.assertFalse(any(pair.exact for pair in matches))
        borderline = lookup(self.output, "I came back.", "mt")
        self.assertTrue(borderline)
        self.assertFalse(borderline[0].exact)
        matches = lookup(self.output, "domani", "mt")
        self.assertTrue(matches)
        self.assertFalse(any(pair.exact for pair in matches))
        self.assertIn("tomorrow", matches[0].en)

    def test_matching_options_bypass_local_ai_and_near_match_supplies_examples(self):
        with patch.object(storage, "DATA", self.folder), patch.object(learning, "generate") as model:
            options = learning.suggest_options("Come to the beach tomorrow", "mt", "fake")
            self.assertEqual(len(options), 2)
            self.assertEqual(options[0].source, "Real Maltese example")
            model.assert_not_called()
            model.return_value = "Tiġix għada."
            learning.suggest_options("Don't come to the beach tomorrow", "mt", "fake")
            self.assertTrue(model.call_args.kwargs["examples"])


class OptionTests(unittest.TestCase):
    def test_click_and_voice_pick_without_pasting(self):
        options = [learning.Suggestion(text, "Real Maltese example") for text in ("Uno", "Dos", "Tres")]
        result = {"text": "Uno", "options": options, "idea": "test", "language": "mt"}
        window = Mock(pending_phrase=(result, "target"), recorder=None)
        window.has_phrase_option.side_effect = lambda index: app.Window.has_phrase_option(window, index)
        app.Window.pick_phrase(window, 1)
        window.preview_phrase.assert_called_once_with({**result, "text": "Dos", "selected": 1}, "target")
        app.Window.voice_confirmation(window, {"text": "Tlieta!"})
        window.pick_phrase.assert_called_once_with(2)
        window.accept_phrase.assert_not_called()
        for number, command in enumerate(("one", "tnejn", "three")):
            self.assertEqual(learning.spoken_option(command), number)


class ExtraEvidenceTests(unittest.TestCase):
    def test_stream_and_merge_preserve_confident_korpus(self):
        with TemporaryDirectory(dir=ROOT) as directory:
            archive = Path(directory) / "words.zip"
            output = Path(directory) / "words.sqlite"
            with ZipFile(archive, "w") as zipped:
                zipped.writestr("words.xml", '<corpus><doc><p lang="mt">'
                               + "ħobż " * 120 + "ħafif " * 120 + "ħabib " * 49
                               + "ġara " * 100 + "gara " * 6 + "affidabilità " * 50
                               + '</p><p lang="en">' + "hafif " * 120 + "</p></doc></corpus>")
            build_words(archive, output)
            with closing(sqlite3.connect(":memory:")) as primary, closing(sqlite3.connect(output)) as extra:
                primary.execute("CREATE TABLE words(word, fold, count)")
                primary.execute("INSERT INTO words VALUES ('hobz','hobz',2000)")
                evidence = Evidence(primary, extra)
                self.assertEqual(evidence.spelling("hobz"), "hobz")
                self.assertEqual(evidence.spelling("hafif"), "ħafif")
                self.assertEqual(evidence.spelling("habib"), "habib")
                self.assertEqual(evidence.spelling("gara"), "gara")
                self.assertEqual(evidence.spelling("għara"), "għara")
                self.assertEqual(evidence.spelling("affidabilita"), "affidabilità")
                self.assertEqual(match_case("mill-Magistrat", "mill-maġistrat"), "mill-Maġistrat")
                evidence.spelling.cache_clear()
