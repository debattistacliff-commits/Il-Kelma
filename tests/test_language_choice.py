import unittest
from ilkelma.engine.stt import choose_language

CATALOG = {
    "en": {"common_words": "the and you is"},
    "mt": {"auto_aliases": ["ar", "tr", "he", "fa"], "common_words": "kif inti qed ħafna bonġu tiegħi"},
    "it": {"common_words": "come stai sono ciao"},
}


class LanguageChoiceTests(unittest.TestCase):
    def test_turkish_label_becomes_maltese(self):
        self.assertEqual(choose_language(CATALOG, {"tr": 0.24, "en": 0.2, "ar": 0.1}, "tr"), "mt")

    def test_maltese_words_win_over_english_label(self):
        self.assertEqual(choose_language(CATALOG, {"en": 0.6, "mt": 0.2}, "en", "bonġu ħabib tiegħi kif inti"), "mt")

    def test_english_stays_english(self):
        self.assertEqual(choose_language(CATALOG, {"en": 0.95}, "en", "hello how are you"), "en")

    def test_italian(self):
        self.assertEqual(choose_language(CATALOG, {"it": 0.8, "en": 0.1}, "it", "ciao come stai"), "it")

    def test_never_outside_packs(self):
        self.assertIn(choose_language(CATALOG, {"de": 0.9, "en": 0.05}, "de"), CATALOG)


if __name__ == "__main__":
    unittest.main()
