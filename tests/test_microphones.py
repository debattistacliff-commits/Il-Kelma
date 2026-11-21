import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

import numpy as np
from PySide6.QtWidgets import QApplication

from ilkelma.app import __main__ as app
from ilkelma.engine import audio


def device(name):
    return SimpleNamespace(description=lambda: name)


class CaptureTests(unittest.TestCase):
    def test_fake_native_float_stream_is_converted_to_mono_wav(self):
        audio_format = audio.QAudioFormat()
        audio_format.setSampleRate(48000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(audio.QAudioFormat.SampleFormat.Float)
        mic = SimpleNamespace(preferredFormat=lambda: audio_format)
        source = Mock()
        source.error.return_value = audio.QAudio.Error.NoError
        stream = source.start.return_value
        stereo = np.array([[0, 0.5], [0, -0.5]], dtype="<f4").tobytes()
        stream.readAll.side_effect = [stereo[:7], stereo[7:]]
        with patch.object(audio, "QAudioSource", return_value=source):
            recorder = audio.Recorder(mic)
            self.assertFalse(recorder.has_samples)
            recorder.receive()
            recorder.close()
        source.stop.assert_called_once()
        with wave.open(io.BytesIO(recorder.wav())) as result:
            self.assertEqual(result.getnchannels(), 1)
            self.assertEqual(result.getframerate(), 48000)
            self.assertEqual(np.frombuffer(result.readframes(2), dtype="<i2").tolist(),
                             [16384, -16384])

    def test_initial_zero_samples_and_later_silence(self):
        recorder = audio.Recorder.__new__(audio.Recorder)
        recorder.test_wav = None
        recorder.last_sound = 10
        recorder.has_samples = False
        recorder.peak = 0
        recorder.keep = False
        recorder.accept(np.zeros((8, 2), dtype=np.float32))
        self.assertFalse(recorder.has_samples)
        self.assertFalse(recorder.silent(12.99))
        self.assertTrue(recorder.silent(13))
        with self.assertRaisesRegex(ValueError, "No sound"):
            recorder.wav()
        with patch.object(audio, "monotonic", return_value=14):
            recorder.accept(np.array([[0, 0.25], [0, -0.25]], dtype=np.float32))
        self.assertTrue(recorder.has_samples)
        self.assertEqual(recorder.level, 0.25)
        self.assertFalse(recorder.silent(16.99))
        self.assertTrue(recorder.silent(17))


class DeviceWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.usb = device("Microphone (USB PnP Audio Device)")
        self.kinect = device("Microphone Array (Kinect USB Audio)")
        self.mixer = device("SplitCam Audio Mixer")
        self.devices = {"kinect": self.kinect, "usb": self.usb, "mixer": self.mixer}
        self.saved = {"language": "en", "voices": False, "mic": self.usb.description() + " (Windows WASAPI)"}
        patches = {
            "microphones": Mock(side_effect=lambda: self.devices.copy()),
            "load": Mock(side_effect=lambda name, fallback: self.saved.copy() if name == "settings" else fallback),
            "save": Mock(),
            "start": Mock(return_value=None),
            "Desktop": Mock(),
            "Buddy": Mock(),
            "Hotkeys": Mock(**{"return_value.bound": {1: "F9", 2: "F10"}}),
        }
        self.enterContext(patch.multiple(app, **patches))
        self.enterContext(patch.dict(os.environ, {"ILKELMA_TEST_AUDIO": ""}))
        self.enterContext(patch.object(self.application, "installNativeEventFilter"))
        self.window = app.Window(self.application)
        self.window.run = Mock()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        if self.window.probes is not None:
            self.window.close_probes()
        self.window.executor.shutdown()
        for widget in (self.window.phrase_panel, self.window):
            widget.hide()
            widget.deleteLater()
        self.window.tray.hide()

    def test_usb_loss_offers_fallback_once_and_usb_returns(self):
        self.assertEqual(self.window.settings["mic_id"], "usb")
        self.assertIn("USB PnP", self.window.tray.toolTip())
        del self.devices["usb"]
        self.window.refresh_devices()
        self.assertEqual(self.window.active_mic, "kinect")
        self.assertIn("Kinect", self.window.setup.text())
        self.assertIn("Kinect", self.window.tray.toolTip())
        self.window.refresh_devices()
        self.window.run.assert_called_once()
        with patch.object(app, "announce") as speak:
            self.window.run.call_args.args[0]()
            speak.assert_called_once_with("Your microphone disconnected. Check Settings.")
        self.devices["usb"] = self.usb
        self.window.refresh_devices()
        self.assertEqual(self.window.active_mic, "usb")
        self.assertIn("USB PnP", self.window.tray.toolTip())

    def test_first_test_measures_all_inputs_and_picks_loudest(self):
        self.window.preferred = None
        self.window.settings["mic"] = None
        probes = {id(self.usb): Mock(peak=0.25), id(self.kinect): Mock(peak=0.01),
                  id(self.mixer): Mock(peak=0.0)}
        with patch.object(app, "Recorder", side_effect=lambda mic, **_: probes[id(mic)]):
            self.window.calibrate_mics()
            self.window.finish_mic_test()
        for probe in probes.values():
            probe.close.assert_called_once()
        self.assertEqual(self.window.settings["mic_id"], "usb")
        self.assertIsNone(audio.loudest_microphone({"kinect": 0, "usb": 0}))

    def test_reconnect_during_fallback_recording_keeps_its_label_until_stop(self):
        del self.devices["usb"]
        self.window.refresh_devices()
        self.window.recording_mic = "kinect"
        self.window.recorder = Mock()
        self.window.show_status("listening", "Listening")
        self.devices["usb"] = self.usb
        self.window.refresh_devices()
        self.assertEqual(self.window.active_mic, "usb")
        self.assertIn("Kinect", self.window.tray.toolTip())
        self.window.cancel()
        self.assertIn("USB PnP", self.window.tray.toolTip())

    def test_silence_warning_keeps_mic_visible_and_pauses_capture_during_speech(self):
        self.window.recording_mic = "usb"
        recorder = self.window.recorder = Mock(level=0.05, source=Mock(), paused=False)
        recorder.source.error.return_value = app.QAudio.Error.NoError
        recorder.finished.is_set.return_value = False
        recorder.silent.return_value = True
        self.window.silence_warned = False
        self.window.update_meter()
        self.window.update_meter()
        self.assertTrue(recorder.paused)
        self.window.buddy.show_message.assert_called_with(
            "I can’t hear you. Check your microphone.", "sleep", None)
        self.assertIn("USB PnP", self.window.tray.toolTip())
        self.assertGreater(self.window.meter.value(), 0)
        self.window.run.assert_called_once()
        speak, resume = self.window.run.call_args.args
        with patch.object(app, "announce") as announce:
            speak()
            announce.assert_called_once_with("I can’t hear you. Check your microphone.")
        resume(None)
        self.assertFalse(recorder.paused)
