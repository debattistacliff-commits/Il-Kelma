import unittest

from ilkelma.app.buddy import feedback, next_language
from ilkelma.engine.comment_reading import comment_language, comment_speech
from ilkelma.languages import DEFAULT_CHOSEN, buddy_message, chosen_codes, packs, set_chosen


class MoreLanguagesTests(unittest.TestCase):
    def tearDown(self):
        set_chosen(DEFAULT_CHOSEN)

    def test_chosen_languages_keep_maltese_and_pack_order(self):
        set_chosen(["it", "es"])
        self.assertEqual(chosen_codes(), ("mt", "it", "es"))
        self.assertEqual(next_language("es"), "mt")
        self.assertEqual(next_language("mt"), "it")

    def test_comments_in_many_languages(self):
        set_chosen(list(packs()))
        for text, code in (("Hola amigos, buenos días a todos", "es"), ("Bonjour tout le monde, merci", "fr"),
                           ("Guten Morgen zusammen", "de"), ("Καλημέρα φίλε", "el"), ("Привіт друже", "uk"),
                           ("مرحبا صديقي", "ar"), ("你好朋友", "zh"), ("Salamat po sa inyo", "tl"),
                           ("Il-għodwa t-tajba lil kulħadd", "mt"), ("Good morning everyone", "en"),
                           ("Buongiorno ragazzi", "it"), ("Dzień dobry wszystkim, dziękuję", "pl")):
            with self.subTest(text=text):
                self.assertEqual(comment_language(text), code)

    def test_unchosen_languages_are_not_picked(self):
        set_chosen(DEFAULT_CHOSEN)
        self.assertIn(comment_language("Hola amigos, buenos días"), ("mt", "en", "it"))

    def test_untranslated_message_uses_english_voice(self):
        self.assertEqual(buddy_message("es", "pasted")[1], "es")
        self.assertEqual(buddy_message("es", "word_saved"), (packs()["en"]["buddy"]["word_saved"], "en"))
        self.assertEqual(feedback("fr", "greeting", user="Clifford", buddy="Kelma")[0],
                         "Bonjour Clifford ! Je suis Kelma.")

    def test_comment_label_in_its_language(self):
        settings = {"comment_author": True, "comment_language": "auto", "comment_speed": "normal"}
        text, voice, _ = comment_speech("Ana", "Hola amigos", "es", settings, lambda t, _l: t)
        self.assertEqual((text, voice), ("Ana dice: Hola amigos", "es"))
