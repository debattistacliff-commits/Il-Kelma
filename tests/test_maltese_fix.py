import unittest
from pathlib import Path
from unittest.mock import patch

from maltese_fix import fix
from maltese_text import detect_language


class MalteseFixTests(unittest.TestCase):
    def test_reported_greeting(self):
        self.assertEqual(fix("Bon ġuħabib tiegħi, kif inti?"), "Bonġu ħabib tiegħi, kif inti?")

    def test_duplicated_greeting(self):
        self.assertEqual(fix("Bong bonġi ħabib tiegħi", lang="mt"), "Bonġu ħabib tiegħi")

    def test_full_transcript(self):
        self.assertEqual(
            fix("Bon ġuħabib tiegħi, kif inti? Issa fl-aħħar ħa nibda nitkellem bil-malti miegħek."),
            "Bonġu ħabib tiegħi, kif inti? Issa fl-aħħar ħa nibda nitkellem bil-Malti miegħek.",
        )

    def test_correction_cases(self):
        cases = [
            ("Il-gurnata kienet sabiha hafna", "Il-ġurnata kienet sabiħa ħafna"),
            ("Nixtieq nghidlek xi haga", "Nixtieq ngħidlek xi ħaġa"),
            ("hafna", "ħafna"),
            ("sabiha", "sabiħa"),
            ("Kien sabiħ hafna", "Kien sabiħ ħafna"),
            ("Tajjeb hafna", "Tajjeb ħafna"),
            ("Jiena ghadni ma nafx.", "Jiena għadni ma nafx."),
            ("Jiena nħobb il-bahar.", "Jiena nħobb il-baħar."),
            ("Kif inti? Jiena sejjer tajjeb hafna.", "Kif inti? Jiena sejjer tajjeb ħafna."),
            ("Inti ghaliex qed tidhak?", "Inti għaliex qed tidħak?"),
            ("Jiena qed naghmel xoghol.", "Jiena qed nagħmel xogħol."),
            ("Jiena naf li gћaliex.", "Jiena naf li għaliex."),
            ("Jiena ghandi hafna xoghol.", "Jiena għandi ħafna xogħol."),
            ("Jiena gћandi hafna xoghol.", "Jiena għandi ħafna xogħol."),
            ("Din haga tajba hafna.", "Din ħaġa tajba ħafna."),
            ("Jiena nara zewg qtates.", "Jiena nara żewġ qtates."),
            ("Jiena rajt gurdien.", "Jiena rajt ġurdien."),
            ("Jiena ghandi bicca hobz.", "Jiena għandi biċċa ħobż."),
            ("Jiena noqghod f Tarxien.", "Jiena noqgħod f Tarxien."),
            ("Jiena noqghod f'Malta.", "Jiena noqgħod f'Malta."),
            ("Jiena qed nikteb bil-malti.", "Jiena qed nikteb bil-Malti."),
            ("Jiena ghandi hafnaxoghol.", "Jiena għandi ħafna xogħol."),
            ("Fejn qieghed sejjer?", "Fejn qiegħed sejjer?"),
            ("xoghol", "xogħol"),
            ("Jiena nitkellem il malti.", "Jiena nitkellem il-Malti."),
            ("il malti", "il-Malti"),
            ("Jiena se mmur id dar.", "Jiena se mmur id-dar."),
            ("Jiena qednikteb.", "Jiena qed nikteb."),
            ("Jiena qed ni kteb.", "Jiena qed nikteb."),
            ("Jiena ma nafxkif inti.", "Jiena ma nafx kif inti."),
            ("Jiena naf kifinti.", "Jiena naf kif inti."),
            ("Jiena ghal iż-żmien.", "Jiena għal iż-żmien."),
            ("  Jiena HAFNA!\n", "  Jiena ĦAFNA!\n"),
            ("Hello! Jiena hafna. Ciao!", "Hello! Jiena ħafna. Ciao!"),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(fix(original), expected)

    def test_unchanged_cases(self):
        cases = [
            "",
            "42!",
            "Good morning, my friend. How are you?",
            "I visited Għawdex and Malta.",
            "The word hafna is on your list.",
            "Take hafna home.",
            "Stephen wrote hafna yesterday.",
            "Buongiorno, amico mio. Come stai?",
            "Oggi vorrei visitare Għawdex e Malta.",
            "Clifford, Tarxien, Froja, Malta, Għawdex.",
            "Jiena qed inkellem lil Clifford u lil FROJA.",
            "Jiena se mmur l-iskola.",
            "Jiena sejjer id-dar.",
            "Jiena naf kelmamaqbuda.",
        ]
        for original in cases:
            with self.subTest(original=original):
                self.assertEqual(fix(original), original)

    def test_personal_words_during_correction(self):
        self.assertEqual(
            fix("Jiena hafna, Clifford, Tarxien, Froja, Malta u Għawdex."),
            "Jiena ħafna, Clifford, Tarxien, Froja, Malta u Għawdex.",
        )

    def test_read_aloud_language(self):
        for text, lang in [
            ("Bonġu, kif inti?", "mt"),
            ("Jiena sejjer tajjeb hafna.", "mt"),
            ("Good morning, my friend!", "en"),
            ("Buongiorno, amico mio!", "it"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(detect_language(text), lang)

    def test_explicit_language_guard(self):
        for lang in ("en", "it"):
            with self.subTest(lang=lang):
                self.assertEqual(fix("Jiena hafna.", lang=lang), "Jiena hafna.")

    def test_learned_spelling_and_language_guards(self):
        self.assertEqual(fix("Jiena rajt dettal.", lang="mt"), "Jiena rajt dettall.")
        self.assertEqual(fix("I wrote dettal yesterday."), "I wrote dettal yesterday.")
        self.assertEqual(fix("Oggi ho scritto dettal."), "Oggi ho scritto dettal.")
        self.assertEqual(fix("Jiena tajjeb.", lang="mt"), "Jiena tajjeb.")

    def test_learned_spelling_respects_personal_words(self):
        read_text = Path.read_text

        def personal_words(path, **kwargs):
            if path.name == "my_words.txt":
                return "dettal\n"
            return read_text(path, **kwargs)

        with patch.object(Path, "read_text", personal_words):
            self.assertEqual(fix("Jiena rajt dettal.", lang="mt"), "Jiena rajt dettal.")


if __name__ == "__main__":
    unittest.main()
