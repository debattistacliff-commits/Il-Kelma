import asyncio
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ilkelma.app.learning import apply_corrections
from ilkelma.app.screen_reader import ScreenReader, comments_key
from ilkelma.app import __main__ as app, buddy_native, storage
from ilkelma.engine.screen_ocr import (
    Line, NewComments, comments, point_box, pointed_text, prepare, read_chat, recognize,
)
from ilkelma.engine.tts import cancellable_audio, play


IMAGES = Path(__file__).with_name("images")


def read_image(name):
    with Image.open(IMAGES / name) as image:
        return asyncio.run(recognize(image))


def live_image(pairs, *, clutter=False):
    image = Image.new("RGB", (420, 390), "#151519")
    draw = ImageDraw.Draw(image)
    font_path = IMAGES.parents[1] / "assets" / "AtkinsonHyperlegible-Regular.ttf"
    small = ImageFont.truetype(font_path, 16)
    large = ImageFont.truetype(font_path, 21)
    for y, name, message in pairs:
        draw.ellipse((10, y + 1, 36, y + 27), fill="#453a90")
        if name.startswith("15 "):
            draw.rounded_rectangle((61, y + 1, 83, y + 18), radius=4, fill="#344070")
            draw.text((64, y), "15", fill="#a4a4a9", font=small)
            draw.text((91, y), name[3:], fill="#a4a4a9", font=small)
        else:
            draw.text((63, y), name, fill="#a4a4a9", font=small)
        draw.text((63, y + 22), message, fill="#f8f8fa", font=large)
    if clutter:
        for x, y, text in [(8, 90, "AB"), (63, 119, "•O"), (63, 144, "a"),
                           (63, 170, "Vantora joined"), (63, 199, "Nerivo 59"),
                           (63, 222, "joined"), (63, 250, "Welcome to Chatsite LIVE"),
                           (63, 278, "Nerivo is LIVE")]:
            draw.text((x, y), text, fill="#f8f8fa", font=large)
        draw.rounded_rectangle((55, 307, 408, 385), radius=10, fill="#24395c")
        draw.text((63, 310), "Buddy says: Echo from the ridge", fill="white", font=large)
        draw.text((63, 345), "Stop", fill="white", font=large)
    return image


def live_rows(pairs, **kwargs):
    return read_chat(asyncio.run(recognize(live_image(pairs, **kwargs), sample_ink=True)),
                     ["Echo from the ridge"])


class CommentControlTests(unittest.TestCase):
    def setUp(self):
        self.application = QApplication.instance() or QApplication([])
        folder = self.enterContext(TemporaryDirectory(dir=storage.DATA.parent))
        self.enterContext(patch.object(storage, "DATA", Path(folder)))
        self.enterContext(patch("ilkelma.app.screen_reader.WordStore"))
        self.timer = self.enterContext(patch("ilkelma.app.screen_reader.QTimer.singleShot"))
        self.window = QObject()
        self.window.buddy = Mock(recent_messages=[])
        self.window.desktop = Mock()
        self.window.foreground_tracker = Mock()
        self.window.foreground_tracker.current.return_value = SimpleNamespace(hwnd=10)
        self.window.desktop.window_at.return_value = 10
        self.window.desktop.comments_window.return_value = ("browser.exe", "Live on Chatsite", (100, 100, 900, 800))
        self.window.hotkeys = Mock()
        self.window.run = Mock()
        self.window.state = "idle"
        self.window.pending_phrase = None
        self.window.settings = {"language": "mt"}
        self.reader = self.window.screen_reader = ScreenReader(self.window)

    def test_toggle_uses_saved_box_then_stops(self):
        self.reader.selected((300, 220, 800, 710))
        self.reader.toggle_comments()
        self.assertIsNone(self.reader.region)
        self.window.buddy.show_message.assert_called_with("Waqaft", "rest")
        self.reader.toggle_comments()
        self.assertEqual(self.reader.region, (300, 220, 800, 710))
        self.assertTrue(self.window.buddy.comments_active)
        self.assertTrue(self.window.buddy.comments_reading)
        self.window.buddy.show_message.assert_called_with("Qed naqra l-kummenti", "read")

    def test_comments_key_normalizes_app_and_page_titles(self):
        for title in ("Chatsite", "X is LIVE - Chatsite LIVE",
                      "X is LIVE | Live on Chatsite ! ", " X is LIVE  |  X -  Chatsite... ",
                      "Chatsite (LIVE)"):
            with self.subTest(title=title):
                self.assertEqual(comments_key("Browser.exe", title), "browser.exe|chatsite")

    def test_saved_box_matches_process_and_title_pattern_after_restart(self):
        self.reader.selected((300, 220, 800, 710))
        self.reader.stop_comments()
        reader = ScreenReader(self.window)
        self.window.desktop.comments_window.return_value = (
            "browser.exe", "Another creator | Chatsite", (150, 50, 950, 750))
        with patch.object(reader, "draw_comments") as draw:
            reader.toggle_comments()
            self.assertEqual(reader.region, (350, 170, 850, 660))
            draw.assert_not_called()
            reader.stop_comments()
            for process, title in (("chatapp.exe", "Chatsite LIVE"), ("browser.exe", "Another website")):
                self.window.desktop.comments_window.return_value = (process, title, (150, 50, 950, 750))
                reader.toggle_comments()
            self.assertEqual(draw.call_count, 2)

    def test_redrawing_replaces_only_the_matching_window_box(self):
        self.reader.selected((300, 220, 800, 710))
        self.reader.stop_comments()
        self.window.desktop.comments_window.return_value = ("browser.exe", "Other chat", (100, 100, 900, 800))
        self.reader.selected((200, 240, 700, 730))
        self.reader.stop_comments()
        self.window.desktop.comments_window.return_value = ("browser.exe", "Chatsite LIVE", (100, 100, 900, 800))
        self.reader.selected((400, 260, 800, 740))
        self.assertEqual(storage.load("comment_boxes", {}), {
            "browser.exe|chatsite": [300, 160, 700, 640],
            "browser.exe|other chat": [100, 140, 600, 630],
        })

    def test_write_and_help_pause_and_resume_without_queued_comments(self):
        window = self.window
        window.status = Mock()
        window.update_mic_display = Mock()
        for pending_phrase in (None, object()):
            with self.subTest(help_preview=pending_phrase is not None):
                self.reader.start_comments((300, 220, 800, 710))
                self.reader.poll()
                _, scanned = window.run.call_args.args
                stop = Event()
                self.reader.speech = stop, "comment"
                self.reader.queue.append(("Sam", "Queued"))
                app.Window.show_status(window, "listening", "Listening")
                self.assertTrue(stop.is_set())
                self.assertTrue(self.reader.paused)
                self.assertFalse(self.reader.queue)
                self.assertFalse(window.buddy.comments_reading)
                self.assertTrue(window.buddy.comments_active)
                self.assertFalse(self.reader.chat_timer.isActive())
                self.reader.speech = None
                window.pending_phrase = pending_phrase
                app.Window.show_status(window, "idle", "Ready")
                self.timer.call_args.args[1]()
                self.assertEqual(self.reader.paused, pending_phrase is not None)
                if pending_phrase is not None:
                    window.pending_phrase = None
                    app.Window.show_status(window, "idle", "Ready")
                    self.timer.call_args.args[1]()
                scanned([Line("Late result", (60, 300, 150, 316), 245)])
                self.assertFalse(self.reader.queue)
                self.assertFalse(self.reader.paused)
                self.assertTrue(self.reader.chat_timer.isActive())
                self.assertTrue(window.buddy.comments_reading)
                self.reader.stop_comments()


class ScreenOcrTests(unittest.TestCase):
    def test_windows_ocr_groups_name_and_message(self):
        self.assertEqual(comments(read_image("chat_first.png")),
                         [("Jenny", "Prosit Doreen"), ("Sam", "Good morning")])

    def test_only_new_comments_after_scrolling(self):
        tracker = NewComments(390)
        first = live_rows([(205, "Velmora", "Clouds are gathering"),
                           (305, "Nerivo", "The ridge is clear")])
        scrolled = live_rows([(55, "Tarnix", "History loaded above"),
                              (205, "Nerivo", "The ridge is clear"),
                              (305, "Vantora", "I can see the valley")])
        self.assertEqual(tracker.update(first), [])
        self.assertEqual(tracker.update(scrolled), [("Vantora", "I can see the valley")])
        self.assertEqual(tracker.update(live_rows([
            (55, "Vantora", "I can see the valley"), (305, "Tarnix", "The ridge is clear")])), [])
        self.assertEqual(tracker.update([]), [])
        self.assertEqual(tracker.update(scrolled), [])
        self.assertEqual(tracker.update(live_rows([(305, "Velmora", "A different screenful")])), [])
        self.assertEqual(tracker.update(live_rows([
            (305, "Velmora", "A different screenful"), (344, "Nerivo", "No scroll here")])),
            [("Nerivo", "No scroll here")])  # chat not full yet: new lines appear below without scrolling

    def test_live_layout_filters_noise_and_keeps_unclear_author_blank(self):
        rows = live_rows([(8, "15 Velmora", "Clouds are gathering"),
                          (52, "A", "The ridge is clear"),
                          (310, "Tarnix", "Hidden by the bubble")], clutter=True)
        self.assertEqual([pair for pair, _ in rows],
                         [("Velmora", "Clouds are gathering"), ("", "The ridge is clear")])

    def test_scroll_skips_previously_seen_text_and_messages_above_bottom(self):
        tracker = NewComments(390)
        tracker.update([(("Velmora", "The ridge is clear"), 150)])
        tracker.update([(("Nerivo", "Clouds are gathering"), 300)])
        rows = [(("Nerivo", "Clouds are gathering"), 170),
                (("Vantora", "This is above the bottom"), 225),
                (("Tarnix", "THE RIDGE IS CLEAR!"), 300),
                (("Velmora", "This is at the bottom"), 365)]
        self.assertEqual(tracker.update(rows), [rows[1][0], rows[-1][0]])

    def test_badge_tokens_are_stripped_from_names(self):
        for badge in ("(All)", "CQSD", "COS)", "A11", "19"):
            with self.subTest(badge=badge):
                lines = [Line(f"{badge} Velmora", (60, 20, 160, 32), 151),
                         Line("The ridge is clear", (60, 38, 190, 54), 245)]
                self.assertEqual(read_chat(lines), [(("Velmora", "The ridge is clear"), 54)])

    def test_capture_affinity_applies_only_to_this_process(self):
        with (patch.object(buddy_native.win32gui, "EnumWindows",
                           side_effect=lambda callback, arg: [callback(hwnd, arg) for hwnd in (1, 2, 3)]),
              patch.object(buddy_native.win32process, "GetWindowThreadProcessId",
                           side_effect=lambda hwnd: (0, -1 if hwnd == 2 else buddy_native.os.getpid())),
              patch.object(buddy_native, "SetWindowDisplayAffinity", side_effect=[True, False]) as affinity,
              patch.object(buddy_native.win32gui, "IsWindowVisible", return_value=True),
              patch.object(buddy_native.win32gui, "GetWindowRect", return_value=(0, 0, 30, 30))):
            self.assertEqual(buddy_native.exclude_app_windows(), ((0, 0, 30, 30),))
            self.assertEqual([call.args for call in affinity.call_args_list], [(1, 0x11), (3, 0x11)])

    def test_stop_clears_queue_and_rejects_an_in_flight_scan(self):
        reader = Mock(tracker=Mock(), outlines=[], region=(0, 0, 420, 390),
                      point_request=None, paused=False, queue=deque([("Velmora", "Queued text")]),
                      speech=(Event(), "comment"), capture_pending=False)
        reader.buddy.recent_messages = []
        ScreenReader.poll(reader)
        _, scanned = reader.window.run.call_args.args
        tracker = reader.tracker
        ScreenReader.stop_comments(reader)
        self.assertTrue(reader.speech[0].is_set())
        self.assertFalse(reader.queue)
        self.assertFalse(reader.buddy.comments_active)
        reader.chat_timer.stop.assert_called_once()
        scanned([Line("Late result", (60, 300, 150, 316), 245)])
        tracker.update.assert_not_called()
        self.assertFalse(reader.queue)

    def test_stop_cancels_synthesis_in_progress(self):
        stop = Event()
        cancelled = Mock()

        async def synthesize(*_):
            try:
                stop.set()
                await asyncio.Future()
            finally:
                cancelled()

        with patch("ilkelma.engine.tts.synthesize", synthesize):
            self.assertIsNone(asyncio.run(cancellable_audio("A message", "en", stop)))
        cancelled.assert_called_once()

    def test_stop_halts_current_playback(self):
        stop = Event()
        mixer = Mock()
        mixer.music.play.side_effect = stop.set
        pygame = SimpleNamespace(mixer=mixer, time=Mock())
        with (patch.dict("sys.modules", pygame=pygame),
              patch("ilkelma.engine.tts.cancellable_audio", return_value=b"audio")):
            play("A message", "en", stop)
        mixer.music.stop.assert_called_once()
        mixer.quit.assert_called_once()

    def test_ignores_system_lines_and_keeps_wrapped_comments(self):
        rows = [
            (0, "Welcome to Chatsite LIVE"),
            (20, "Ally joined"),
            (40, "Somebody liked the LIVE"),
            (52, "Jenny"),
            (69, "Congratulations on"),
            (84, "your work!"),
            (120, "Luca"),
            (137, "Ciao a tutti"),
        ]
        lines = [Line(text, (10, y, 100, y + 11)) for y, text in rows]
        self.assertEqual(comments(lines), [("Jenny", "Congratulations on your work!"), ("Luca", "Ciao a tutti")])

    def test_scrolled_area_can_start_with_a_partial_comment(self):
        lines = [Line("Good morning", (10, 0, 106, 12)),
                 Line("Jenny", (10, 30, 46, 41)),
                 Line("Prosit Doreen", (10, 47, 112, 60))]
        self.assertEqual(comments(lines), [("Jenny", "Prosit Doreen")])

    def test_fixes_maltese_and_uses_personal_corrections(self):
        correction = Mock(side_effect=lambda text, language: apply_corrections(text, {"it-tifla": "it-bint"}))
        source = comments(read_image("chat_scrolled.png"))[-1][1]
        self.assertEqual(prepare(source, "en", correction), ("jien għandi it-bint", "mt"))
        correction.assert_called_once_with("jien għandi it-tifla", "mt")
        for text, fallback, wanted in [
            ("Good morning", "mt", "en"), ("Ciao a tutti", "en", "it"),
            ("Prosit Doreen", "mt", "mt"), ("Doreen", "it", "it"),
        ]:
            self.assertEqual(prepare(text, fallback, lambda value, _: value), (text, wanted))

    def test_point_reads_nearby_paragraph_and_clamps_capture(self):
        lines = read_image("point_paragraph.png")
        self.assertEqual(pointed_text(lines, (60, 67)), "This is the first line and this is the next line")
        self.assertEqual(pointed_text(lines, (330, 68)), "Another column")
        self.assertEqual(pointed_text(lines, (240, 140)), "")
        self.assertEqual(point_box((-1915, 100), (-1920, 0, 0, 1080)), (-1920, 0, -1535, 210))


if __name__ == "__main__":
    unittest.main()
