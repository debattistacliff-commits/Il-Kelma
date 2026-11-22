from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import unittest
from unittest.mock import Mock, call, patch

from ilkelma.api import server_for
from ilkelma.engine.language import detect
from ilkelma.engine.target import remember
from ilkelma.engine.tidy import punctuate, tidy
from ilkelma.languages import packs


class FakeDesktop:
    active = 10
    pointed = 20
    clicked = None
    processes = {10: 100, 20: 200}

    def foreground(self):
        return self.active

    def window_at(self, point):
        return self.pointed

    def exists(self, hwnd):
        return hwnd in self.processes

    def process(self, hwnd):
        return self.processes[hwnd]

    def activate(self, hwnd):
        self.active = hwnd
        return True

    def focus_at(self, point):
        self.clicked = point


class TargetTests(unittest.TestCase):
    def test_original_window(self):
        desktop = FakeDesktop()
        target = remember(desktop)
        desktop.active = 20
        self.assertTrue(target.focus(desktop))
        self.assertEqual(desktop.active, 10)
        self.assertIsNone(desktop.clicked)

    def test_clipped_point(self):
        desktop = FakeDesktop()
        target = remember(desktop, (320, 231))
        self.assertTrue(target.focus(desktop))
        self.assertEqual(desktop.active, 20)
        self.assertEqual(desktop.clicked, (320, 231))

    def test_point_now_covers_another_window(self):
        desktop = FakeDesktop()
        target = remember(desktop, (320, 231))
        desktop.pointed = 10
        self.assertFalse(target.focus(desktop))
        self.assertIsNone(desktop.clicked)

    def test_window_reused_by_another_process(self):
        desktop = FakeDesktop()
        target = remember(desktop)
        desktop.processes = {10: 999}
        self.assertFalse(target.focus(desktop))


class ActivationTests(unittest.TestCase):
    def test_activation_fallbacks(self):
        from ilkelma.engine import windows

        for success_at in (0, 1, 2, 3, None):
            with (
                self.subTest(success_at=success_at),
                patch.object(windows, "win32gui") as gui,
                patch.object(windows, "win32api") as api,
                patch.object(windows, "win32process") as process,
            ):
                desktop = windows.Desktop()
                active = 10 if success_at == 0 else 20
                gui.GetForegroundWindow.side_effect = lambda: active
                gui.IsIconic.return_value = False
                gui.GetAncestor.side_effect = lambda hwnd, flag: hwnd
                process.GetWindowThreadProcessId.return_value = (31, 200)
                api.GetCurrentThreadId.return_value = 32

                def set_foreground(hwnd):
                    nonlocal active
                    if gui.SetForegroundWindow.call_count == success_at:
                        active = hwnd

                gui.SetForegroundWindow.side_effect = set_foreground
                self.assertEqual(desktop.activate(10), success_at is not None)
                self.assertEqual(gui.SetForegroundWindow.call_count, 3 if success_at is None else success_at)
                if success_at in (2, 3, None):
                    api.keybd_event.assert_has_calls([
                        call(windows.VK_F24, 0, 0, 0),
                        call(windows.VK_F24, 0, windows.con.KEYEVENTF_KEYUP, 0),
                    ])
                else:
                    api.keybd_event.assert_not_called()
                if success_at in (3, None):
                    self.assertEqual(process.AttachThreadInput.call_args_list, [
                        call(32, 31, True), call(32, 31, False),
                    ])
                else:
                    process.AttachThreadInput.assert_not_called()

    def test_paste_keeps_clipboard_when_focus_fails(self):
        from ilkelma.engine import windows

        desktop = windows.Desktop(30)
        target = Mock(hwnd=10)
        target.focus.return_value = False
        with (patch.object(desktop, "describe", return_value="window"),
              patch.object(desktop, "foreground", return_value=20),
              patch.object(windows, "put_text") as put_text,
              patch.object(windows, "keys") as keys):
            self.assertFalse(desktop.paste(target, "test"))
            put_text.assert_called_once_with("test", 30)
            keys.assert_not_called()


class RecorderTests(unittest.TestCase):
    def test_test_audio_uses_wav_duration_without_opening_microphone(self):
        import io
        import os
        from tempfile import TemporaryDirectory
        import wave

        from ilkelma.engine import audio

        output = io.BytesIO()
        with wave.open(output, "wb") as source:
            source.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            source.writeframes(bytes(4000))
        with TemporaryDirectory(dir=Path.cwd()) as folder:
            clip = Path(folder) / "sample.wav"
            clip.write_bytes(output.getvalue())
            with (patch.dict(os.environ, {"ILKELMA_TEST_AUDIO": str(clip)}),
                  patch.object(audio, "QAudioSource") as microphone,
                  patch.object(audio, "Timer") as timer,
                  self.assertLogs(level="INFO") as logs):
                recorder = audio.Recorder(None)
                timer.assert_called_once_with(0.25, recorder.finished.set)
                self.assertFalse(recorder.finished.is_set())
                timer.call_args.args[1]()
                self.assertEqual(recorder.wav(), output.getvalue())
                microphone.assert_not_called()
                self.assertIn("TEST AUDIO MODE", logs.output[0])


class TextTests(unittest.TestCase):
    def test_languages_and_order(self):
        self.assertEqual(list(packs())[:3], ["en", "mt", "it"])
        self.assertGreaterEqual(len(packs()), 17)
        for text, code in (("Nixtieq ngħidlek xi ħaġa", "mt"),
                           ("Good morning, how are you?", "en"),
                           ("Buongiorno, vorrei raccontarti qualcosa", "it")):
            with self.subTest(code=code):
                self.assertEqual(detect(text), code)

    def test_spacing(self):
        self.assertEqual(tidy("  kelma   oħra\n"), "kelma oħra")


    def test_punctuation_and_capitals(self):
        for original, language, expected in (
            ("good morning. would you like some tea?", "en", "Good morning. Would you like some tea?"),
            ("buongiorno", "it", "Buongiorno."),
            ("kif inti", "mt", "Kif inti?"),
            ("fejn hi malta.", "mt", "Fejn hi Malta?"),
            ("x'qed tagħmel", "mt", "X'qed tagħmel?"),
            ("tajjeb, hux", "mt", "Tajjeb, hux?"),
            ("bonġu, kif inti", "mt", "Bonġu, kif inti?"),
            ("Bonġu kif inti illum il-ġurnata sabiħa ħafna u ħa nmur il-baħar.", "mt",
             "Bonġu, kif inti? Illum il-ġurnata sabiħa ħafna, u ħa nmur il-baħar."),
            ("Bonswa kif int issa sejjer ix-xogħol", "mt", "Bonswa, kif int? Issa sejjer ix-xogħol."),
            ("Ħello kif aħna jien lest", "mt", "Ħello, kif aħna? Jien lest."),
            ("Bonġu kif inti sejjer", "mt", "Bonġu, kif inti sejjer?"),
            ("Ħello, kif int? Jien lest.", "mt", "Ħello, kif int? Jien lest."),
            ("nitkellem bil-malti", "mt", "Nitkellem bil-Malti."),
            ("", "mt", ""),
        ):
            with self.subTest(original=original):
                self.assertEqual(punctuate(original, language, Path("missing-test-words")), expected)

    def test_personal_capitals(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(dir=Path.cwd()) as folder:
            personal = Path(folder) / "my_words.txt"
            personal.write_text("Froja\nGħawdex\n", encoding="utf-8", newline="\n")
            self.assertEqual(punctuate("rajna froja f'għawdex", "mt", personal),
                             "Rajna Froja f'Għawdex.")


class FakeEngine:
    def fix(self, text):
        return text.upper()

    def transcribe(self, audio, language):
        return {"text": audio.decode(), "language": language or "en"}

    def speak(self, text, language):
        return b"mp3"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server_for(FakeEngine(), 0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, content_type=None, **headers):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        return urlopen(Request(self.url + path, body, headers), timeout=5)

    def test_health(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.request("/health") as response:
            self.assertEqual(json.load(response)["status"], "ok")

    def test_fix(self):
        with self.request("/fix", {"text": "xi ħaġa"}) as response:
            self.assertEqual(json.load(response), {"text": "XI ĦAĠA"})

    def test_speak(self):
        with self.request("/speak", {"text": "hi", "lang": "en"}) as response:
            self.assertEqual(response.headers.get_content_type(), "audio/mpeg")
            self.assertEqual(response.read(), b"mp3")

    def test_transcribe_raw(self):
        with self.request("/transcribe?lang=mt", b"sample", "audio/wav") as response:
            self.assertEqual(json.load(response), {"text": "sample", "language": "mt"})

    def test_transcribe_multipart(self):
        audio = (b'--separator\r\nContent-Disposition: form-data; name="file"; filename="test.wav"'
                 b'\r\nContent-Type: audio/wav\r\n\r\nsample\r\n--separator--\r\n')
        with self.request("/transcribe", audio, "multipart/form-data; boundary=separator") as response:
            self.assertEqual(json.load(response)["text"], "sample")

    def test_refuse_browser_origin(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/fix", {"text": "hi"}, Origin="https://example.com")
        self.assertEqual(error.exception.code, 403)

    def test_bad_language(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/speak", {"text": "hi", "lang": "bad"})
        self.assertEqual(error.exception.code, 400)


class WaveTest(unittest.TestCase):
    @unittest.skipUnless(all(Path("tests/audio", f"{code}.wav").is_file() for code in ("en", "mt", "it")),
                         "Generate the local benchmark WAVs first.")
    def test_real_waves_in_engine(self):
        from ilkelma.engine.stt import Engine

        engine = Engine()
        for code, expected in (("en", ("good morning", "my day")),
                               ("it", ("buongiorno", "mia giornata")),
                               ("mt", ("il-ġurnata kienet sabiħa ħafna", "ngħidlek xi ħaġa"))):
            with self.subTest(language=code):
                result = engine.transcribe(Path("tests/audio", f"{code}.wav").read_bytes())
                self.assertEqual(result["language"], code)
                for phrase in expected:
                    self.assertIn(phrase, result["text"].lower())

    @unittest.skipUnless(Path("models/parakeet-tdt-0.6b-v3/encoder-model.int8.onnx").is_file(),
                         "Download the optional Parakeet model first.")
    def test_parakeet_engine(self):
        from ilkelma.engine.stt import Engine

        result = Engine(backend="parakeet").transcribe(Path("tests/audio/en.wav").read_bytes(), "en")
        self.assertEqual(result["language"], "en")
        self.assertIn("Good morning", result["text"])

    @unittest.skipUnless(Path("tests/audio/clifford.wav").is_file(), "Private test audio is local only.")
    def test_private_recording_routes_to_maltese(self):
        from ilkelma.engine.stt import Engine

        audio = Path("tests/audio/clifford.wav").read_bytes()
        self.assertEqual(Engine().transcribe(audio)["language"], "mt")
