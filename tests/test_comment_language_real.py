import unittest

from ilkelma.engine.comment_reading import comment_language


class RealCommentLanguageTests(unittest.TestCase):
    """Invented comments shaped like live web chat."""

    def test_languages(self):
        cases = {
            "Andi il guh ima andi l istonku": "mt",      # Maltese without Maltese letters
            "Kif inti lovely ? ok issa ma niflaħx": "mt",
            "Fejna il blondie": "mt",
            "hello beautiful people": "en",              # English loans also live in the Maltese corpus
            "good morning everyone": "en",
            "ciao bella come stai": "it",
            "grazzi ħafna, love you": "mt",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(comment_language(text), expected)


if __name__ == "__main__":
    unittest.main()
