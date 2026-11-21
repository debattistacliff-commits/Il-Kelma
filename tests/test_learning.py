import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ilkelma.app import __main__ as app, learning, storage
from ilkelma.engine import phrasing
from ilkelma.engine.stt import Engine


class PhraseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=storage.DATA.parent)
        self.addCleanup(temporary.cleanup)
        seed = storage.load("phrasebook", {})["seed"]
        patcher = patch.object(storage, "DATA", Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        storage.save("phrasebook", {"seed": seed, "confirmed": {}})

    @patch("ilkelma.app.learning.generate")
    def test_seed_exact_close_and_mixed(self, generate):
        self.assertEqual(learning.suggest("YOU are right my friend, in Maltese!", "mt", "fake"),
                         "Għandek raġun, ħabib tiegħi.")
        self.assertEqual(learning.suggest("Grazzi hafna", "it", "fake"), "Grazie mille.")
        self.assertEqual(learning.suggest("ghidlu grazzi hafna for the party", "mt", "fake"),
                         "Grazzi ħafna tal-festin.")
        generate.assert_not_called()

    @patch("ilkelma.app.learning.generate", return_value="I can't help.")
    def test_new_meaning_is_not_matched_by_approximation(self, generate):
        self.assertEqual(learning.suggest("You are not right my friend", "en", "fake"), "I can't help.")
        generate.assert_called_once_with("You are not right my friend", "en", "fake")

    @patch("ilkelma.app.learning.generate")
    def test_confirmed_phrase_is_identical_and_language_specific(self, generate):
        learning.confirm_phrase("my own words", "mt", "Il-kliem tiegħi!")
        self.assertEqual(learning.suggest("My own words.", "mt", "fake"), "Il-kliem tiegħi!")
        generate.assert_not_called()
        generate.return_value = "Le mie parole."
        self.assertEqual(learning.suggest("my own words", "it", "fake"), "Le mie parole.")

    @patch("ilkelma.engine.phrasing.urlopen")
    def test_local_ai_request_and_maltese_cleanup(self, open_url):
        open_url.return_value = io.BytesIO(json.dumps({
            "message": {"content": "Ghandek ragun. Habib tieghi!"}
        }).encode())
        with patch.object(phrasing, "fix", return_value="Għandek raġun, ħabib tiegħi!") as fix:
            self.assertEqual(phrasing.generate("my idea", "mt", "fake"),
                             "Għandek raġun, ħabib tiegħi!")
        fix.assert_called_once_with("Ghandek ragun, Habib tieghi!", lang="mt")
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, phrasing.LOCAL_AI_URL)
        self.assertEqual(body["model"], "fake")
        self.assertEqual(body["keep_alive"], 0)
        self.assertIn("Maltese", body["messages"][0]["content"])

    def test_corrections_persist_utf8_and_apply_once_by_language(self):
        learning.learn_word("mt", "klarah", "Clara", name=True)
        learning.learn_word("mt", "bongu", "bonġu")
        learning.learn_word("mt", "clarA", "Marija")
        learning.learn_word("it", "bongu", "buongiorno")
        self.assertEqual(learning.corrected("Klarah, BONGU! karbongu tal-bongu", "mt"),
                         "Clara, BONĠU! karbongu tal-bongu")
        self.assertEqual(learning.corrected("Bongu", "it"), "Buongiorno")
        self.assertEqual(learning.corrected("Bongu", "en"), "Bongu")
        self.assertEqual((storage.DATA / "my_words.txt").read_bytes(), b"Clara\n")
        self.assertIn("bonġu".encode(), (storage.DATA / "my_corrections.json").read_bytes())


class WindowFlowTests(unittest.TestCase):
    def test_help_records_without_forcing_output_language_onto_speech(self):
        window = Mock(recorder=None, pending_phrase=None, settings={"language": ""})
        app.Window.help_with_phrase(window, "original typing box")
        window.start_recording.assert_called_once_with("phrase", "original typing box", "mt")
        engine = Engine()
        engine.last_language_probs = {}
        with patch.object(engine, "raw", return_value=("Grazzi for the party", "en")) as raw:
            self.assertEqual(engine.transcribe_idea(b"audio")["text"], "Grazzi for the party")
        self.assertEqual(len(raw.call_args.args), 2)

    def test_preview_does_not_paste_and_reads_the_selected_sentence(self):
        window = Mock()
        window.buddy.screen.return_value.availableGeometry.return_value = app.QRect(0, 0, 800, 600)
        window.phrase_panel.rect.return_value = app.QRect(0, 0, 610, 200)
        result = {"text": "Għandek raġun.", "language": "mt", "idea": "you're right"}
        app.Window.preview_phrase(window, result, "original typing box")
        window.phrase_text.setText.assert_called_once_with("Għandek raġun.")
        window.voice_button.setEnabled.assert_called_once_with(False)
        speak, spoken = window.run.call_args.args
        speak()
        spoken(None)
        window.read_phrase.assert_called_once_with(result, window.phrase_audio)
        window.voice_button.setEnabled.assert_called_with(True)
        window.paste_text.assert_not_called()

    def test_voice_requires_iva_alone(self):
        window = Mock(pending_phrase=object())
        app.Window.voice_confirmation(window, {"text": "Le, iva."})
        app.Window.voice_confirmation(window, {"text": "Iva in Maltese."})
        window.accept_phrase.assert_not_called()
        app.Window.voice_confirmation(window, {"text": "Iva!"})
        window.accept_phrase.assert_called_once_with()

    @patch("ilkelma.app.__main__.monotonic", return_value=10)
    def test_confirmation_checks_after_the_user_pauses(self, _):
        recorder = Mock(level=0.05, peak=0.05, last_sound=9.8, source=Mock())
        recorder.finished.is_set.return_value = False
        recorder.source.error.return_value = app.QAudio.Error.NoError
        window = Mock(recorder=recorder, capture_mode="confirm")
        app.Window.update_meter(window)
        window.finish_recording.assert_not_called()
        recorder.last_sound = 9
        app.Window.update_meter(window)
        window.finish_recording.assert_called_once_with()

    @patch("ilkelma.app.__main__.confirm_phrase")
    def test_accept_remembers_then_pastes_once_to_original_target(self, confirm):
        result = {"text": "Għandek raġun.", "language": "mt", "idea": "you're right"}
        window = Mock(recorder=None, pending_phrase=(result, "original typing box"))
        window.dismiss_phrase.side_effect = lambda: setattr(window, "pending_phrase", None)
        app.Window.accept_phrase(window)
        app.Window.accept_phrase(window)
        confirm.assert_called_once_with(result["idea"], "mt", result["text"])
        window.paste_text.assert_called_once_with(result, "original typing box")

    def test_help_shortcut_falls_back(self):
        window = Mock(settings={"help_key": "F8"})
        window.hotkeys.bind.side_effect = lambda slot, key: key != "F8"
        self.assertEqual(app.Window.register_help_shortcut(window), "Use Ctrl+Alt+H for Help me say it.")
        window.show_active_key.assert_called_once_with(4)

    def test_settings_opens_once_and_utf8_logging_is_explicit(self):
        with (patch.object(app, "QApplication"), patch.object(app, "QLockFile"),
              patch.object(app, "QFontDatabase"), patch.object(app, "Window") as window,
              patch.object(app, "load", side_effect=[None, {}]), patch.object(app, "save") as save,
              patch.object(app.logging, "basicConfig") as logs):
            window.return_value.state = "idle"
            app.main()
            window.return_value.show.assert_called_once_with()
            save.assert_called_once()
            app.main()
            window.return_value.show.assert_called_once_with()
            self.assertEqual(logs.call_args.kwargs["encoding"], "utf-8")

