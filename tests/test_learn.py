import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ilkelma.app import storage
from ilkelma.app.learn import Checked, entry_id, find, load_book


class LearnTests(unittest.TestCase):
    def test_book_has_five_categories_with_full_entries(self):
        book = load_book()
        self.assertEqual([c["id"] for c in book], ["idjomi", "qwiel", "verbi", "pronomi", "artiklu", "ortografija"])
        for category in book:
            for entry in category["entries"]:
                for field in ("text", "meaning_mt", "meaning_en", "example"):
                    self.assertTrue(entry[field].strip(), f"{category['id']} {field}")

    def test_voice_question_finds_the_idiom(self):
        book = load_book()
        for heard, expected in (("X'tfisser qiegħed fuq ix-xwiek?", "Qiegħed fuq ix-xwiek"),
                                ("xi tfisser qalbu tad deheb", "Għandu qalbu tad-deheb"),
                                ("x'jigifieri mhux kull ma jleqq deheb", "Mhux kull ma jleqq deheb")):
            with self.subTest(heard=heard):
                category, index = find(book, heard)
                self.assertEqual(category["entries"][index]["text"], expected)
        self.assertIsNone(find(book, "il-karozza ħamra"))
        category, index = find(book, "kif tikkonjuga nieħu")
        verb = category["entries"][index]
        self.assertEqual(verb["tables"][1]["rows"][3], ["hija", "ħadet", "ma ħaditx"])
        self.assertIn("ma ħadniex", __import__("ilkelma.app.learn", fromlist=["x"]).tables_html(verb["tables"]))

    def test_checked_entries_are_saved(self):
        with tempfile.TemporaryDirectory(dir=storage.DATA.parent) as folder:
            with patch.object(storage, "DATA", Path(folder)):
                category = load_book()[0]
                checked = Checked()
                self.assertNotIn(entry_id(category, 1), checked)
                checked.add(entry_id(category, 1))
                self.assertIn("idjomi-1", Checked())


class BookSpellingTests(unittest.TestCase):
    def test_book_words_are_spelled_right(self):
        from ilkelma.app.pad_text import fix_letters
        from ilkelma.learn import book_patterns
        self.assertEqual(book_patterns()["tihdux"], "tiħdux")
        self.assertNotIn("hu", book_patterns())  # 'hu' (he) is a real word too
        self.assertEqual(fix_letters("ma tihdux, ma hadniex"), "ma tiħdux, ma ħadniex")


class QuizTests(unittest.TestCase):
    def test_questions_from_verbs_and_idioms(self):
        import random
        from ilkelma.app.learn import make_questions
        questions = make_questions(load_book(), random.Random(1))
        prompts = {q["prompt"]: q["answer"] for q in questions}
        self.assertEqual(prompts["she does not take"], "ma tiħux")
        self.assertEqual(prompts["we took"], "ħadna")
        self.assertEqual(prompts["Take! (to more people)"], "ħudu")
        chosen = [q for q in questions if q["kind"] == "choose"]
        self.assertTrue(all(q["answer"] in q["choices"] for q in chosen))
        self.assertEqual(len(chosen), 5)

    def test_answers_teach_the_letters(self):
        from ilkelma.app.learn import check
        self.assertEqual(check("ħadna", "Ħadna."), "right")
        self.assertEqual(check("ħadna", "hadna"), "letters")
        self.assertEqual(check("ma tiħux", "ma tihux"), "letters")
        self.assertEqual(check("ħadna", "ħadu"), "wrong")


class VerbMakerTests(unittest.TestCase):
    def test_rules_make_kiteb(self):
        from ilkelma.learn.verbs import all_verbs
        tables = all_verbs()["kiteb"][0]
        self.assertEqual(tables[0]["rows"][2], ["huwa", "jikteb", "ma jiktibx"])
        self.assertEqual(tables[1]["rows"][3], ["hija", "kitbet", "ma kitbitx"])
        self.assertEqual(tables[1]["rows"][4], ["aħna", "ktibna", "ma ktibniex"])
        self.assertEqual(tables[2]["rows"][1], ["intom", "iktbu", "tiktbux"])
        self.assertEqual(all_verbs()["xorob"][0][1]["rows"][3][1], "xorbot")


class SpellAndTalkTests(unittest.TestCase):
    def test_underlines_possible_mistakes(self):
        from ilkelma.engine.spellcheck import unknown_spans
        text = "Jien hadt il-ktieb u mort il-bahar. Żfinaw. Il-familja ġiet għall-baħar."
        self.assertEqual([text[a:b] for a, b in unknown_spans(text)], ["hadt", "bahar", "Żfinaw"])

    def test_same_sentence_ignores_case_and_marks(self):
        from ilkelma.engine.conversation import same
        self.assertTrue(same("bongu kelma", "Bongu, Kelma!"))
        self.assertFalse(same("mort il bahar", "Mort il-baħar."))


class VoiceLearningTests(unittest.TestCase):
    def test_only_letter_fixes_are_learned_by_voice(self):
        from unittest.mock import Mock
        from ilkelma.app import pad
        host = Mock(language="mt")
        with patch.object(pad, "learn_word") as learn:
            pad.Pad.remember_spelling(host, "hadt", "hot", by_voice=True)
            learn.assert_not_called()
            pad.Pad.remember_spelling(host, "hadt", "ħadt", by_voice=True)
            learn.assert_called_once_with("mt", "hadt", "ħadt")
