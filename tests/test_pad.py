import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from ilkelma.app.__main__ import Window
from ilkelma.app.pad import FONTS, HEADER_DARK, HEADER_LIGHT, MARKS, SAVED_TEXT, THEMES, Pad, qt_position
from ilkelma.app.pad_widgets import DockAction, ToggleSwitch, gradient
from ilkelma.engine import tts
from ilkelma.engine.target import Target
from ilkelma.engine.windows import Desktop


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = {"language": "mt"}
        self.pending = None
        self.pad_capture = Mock()
        self.cancel_pad_capture = Mock()
        self.paste_pad = Mock()

    def change(self, name, value):
        self.settings[name] = dict(value)

    def run(self, work, finished):
        self.pending = (work, finished)


class PadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.folder.cleanup)
        self.host = Host()
        self.pad = Pad(self.host, self.folder.name)
        self.addCleanup(self.dispose_host)
        self.pad.show()
        self.application.processEvents()

    def dispose_host(self):
        self.pad.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_open_close_and_restore(self):
        self.pad.toggle_panel()
        area = self.pad.screen().availableGeometry()
        self.pad.resize(min(1250, area.width() - 60), min(730, area.height() - 60))
        self.pad.move(area.topLeft() + QPoint(20, 25))
        self.pad.editor.setPlainText("Test")
        self.application.processEvents()
        size, position = self.pad.size(), self.pad.pos()
        self.assertTrue(self.pad.close())
        self.assertFalse(self.pad.autosave.isActive())
        restored = Pad(self.host, self.folder.name)
        self.addCleanup(restored.close)
        restored.show()
        self.application.processEvents()
        self.assertEqual(restored.size(), size)
        self.assertEqual(restored.pos(), position)
        self.assertEqual(restored.editor.toPlainText(), "Test")
        self.assertEqual(restored.language, "mt")

    def test_look_changes_and_persists(self):
        for name, value in (("theme", "Bold"), ("font", "Andika"), ("spacing", "Extra"),
                            ("size", 40), ("name", "Il-Pinna")):
            self.pad.set_option(name, value)
        font = self.pad.editor.document().defaultFont()
        self.assertEqual(font.family(), "Andika")
        self.assertEqual(font.pixelSize(), 40)
        self.assertEqual(font.letterSpacing(), 2.5)
        self.assertEqual(font.wordSpacing(), 10)
        self.assertEqual(self.pad.editor.document().firstBlock().blockFormat().lineHeight(), 220)
        self.assertIn("#FFE600", self.pad.styleSheet())
        self.assertEqual(self.pad.windowTitle(), "Il-Pinna")
        self.assertFalse(self.pad.panel.isVisible())
        self.assertEqual(self.host.settings["pad"]["font"], "Andika")

    def test_toggle_switch_animates_to_both_endpoints(self):
        switch = ToggleSwitch()
        self.assertEqual(switch.knobX, 3)
        switch.click()
        self.assertTrue(switch.isChecked())
        switch.animation.setCurrentTime(switch.animation.duration())
        self.assertEqual(switch.knobX, 27)
        switch.click()
        self.assertFalse(switch.isChecked())
        switch.animation.setCurrentTime(switch.animation.duration())
        self.assertEqual(switch.knobX, 3)

    def test_settings_overlay_opens_and_closes_without_resizing_paper(self):
        original = self.pad.paper.geometry()
        self.assertFalse(self.pad.panel.isVisible())
        self.pad.panel_button.click()
        animation = self.pad.panel_animation
        animation.setCurrentTime(animation.duration())
        self.assertTrue(self.pad.panel.isVisible())
        self.assertTrue(self.pad.panel_button.isChecked())
        self.assertEqual(self.pad.panel.pos(), self.pad.panel_position())
        self.assertEqual(self.pad.paper.geometry(), original)
        self.pad.panel_button.click()
        animation.setCurrentTime(animation.duration())
        self.assertFalse(self.pad.panel.isVisible())
        self.assertFalse(self.host.settings["pad"]["panel"])

    def test_panel_leaves_dock_clickable_at_minimum_and_wide_sizes(self):
        animation = self.pad.panel_animation
        chat = next(button for button in self.pad.dock.findChildren(DockAction)
                    if button.text().startswith("Put it in the chat"))
        self.pad.editor.setPlainText("Hello")
        minimum = self.pad.minimumSizeHint().expandedTo(self.pad.minimumSize())
        for width in (minimum.width(), 1500):
            with self.subTest(width=width):
                self.pad.resize(width, minimum.height())
                self.application.processEvents()
                centered_x = self.pad.dock.x()
                self.pad.toggle_panel()
                for time in (0, animation.duration() // 2, animation.duration()):
                    animation.setCurrentTime(time)
                    self.assertFalse(self.pad.panel.geometry().intersects(self.pad.dock.geometry()))
                if width == 1500:
                    self.assertLess(self.pad.dock.x(), centered_x)
                point = chat.mapTo(self.pad, chat.rect().center())
                self.assertIs(self.pad.childAt(point), chat)
                self.host.paste_pad.reset_mock()
                QTest.mouseClick(chat, Qt.MouseButton.LeftButton)
                self.host.paste_pad.assert_called_once_with("Hello")
                self.pad.toggle_panel()
                animation.setCurrentTime(animation.duration() // 2)
                self.assertFalse(self.pad.panel.geometry().intersects(self.pad.dock.geometry()))
                animation.setCurrentTime(animation.duration())
                self.assertEqual(self.pad.dock.x(), centered_x)

    def test_font_cards_and_settings_scroll_at_minimum_height(self):
        self.pad.resize(self.pad.minimumSizeHint().expandedTo(self.pad.minimumSize()))
        self.pad.toggle_panel()
        animation = self.pad.panel_animation
        animation.setCurrentTime(animation.duration())
        self.application.processEvents()
        for family in FONTS:
            with self.subTest(family=family):
                card = self.pad.choices["font", family]
                layout = card.layout()
                sample, name = (layout.itemAt(index).widget() for index in range(2))
                self.assertGreaterEqual(card.height(), 64)
                self.assertEqual(sample.text(), "Għandek raġun")
                self.assertGreaterEqual(name.y() - sample.geometry().bottom() - 1, 4)
                for line in (sample, name):
                    self.assertGreaterEqual(line.height(), line.sizeHint().height())
                    self.assertGreaterEqual(line.width(), line.sizeHint().width())
                    self.assertTrue(card.rect().adjusted(12, 12, -12, -12).contains(line.geometry()))
        bar = self.pad.panel.verticalScrollBar()
        self.assertTrue(bar.isVisible())
        self.assertGreater(bar.maximum(), 0)
        for widget in (*self.pad.switches.values(), self.pad.choices["name", "Kelma Pad"]):
            self.pad.panel.ensureWidgetVisible(widget, 0, 0)
            viewport = self.pad.panel.viewport()
            self.assertTrue(viewport.rect().contains(widget.rect().translated(widget.mapTo(viewport, QPoint()))))

    def test_title_contrast_on_every_theme(self):
        def luminance(colour):
            return sum(weight * (value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4)
                       for weight, value in zip((.2126, .7152, .0722), colour.getRgbF()[:3]))

        for theme in THEMES:
            with self.subTest(theme=theme):
                self.pad.set_option("theme", theme)
                header = HEADER_DARK if theme in ("Night", "Bold") else HEADER_LIGHT
                self.assertIn(f"#padHeader {{background:{header};}}", self.pad.styleSheet())
                background = luminance(QColor(header))
                stops = gradient(self.pad.title.rect().toRectF(), *self.pad.title.colours).stops()
                first, last = (colour.getRgb()[:3] for _, colour in stops)
                for step in range(101):
                    colour = QColor(*(round(a + (b - a) * step / 100) for a, b in zip(first, last)))
                    low, high = sorted((luminance(colour), background))
                    self.assertGreaterEqual((high + .05) / (low + .05), 4.5)

    def test_first_open_uses_centered_eighty_percent_of_work_area(self):
        area = self.host.screen().availableGeometry()
        minimum = self.pad.minimumSizeHint().expandedTo(self.pad.minimumSize())
        self.assertEqual(self.pad.width(), max(minimum.width(), round(area.width() * .8)))
        self.assertEqual(self.pad.height(), max(minimum.height(), round(area.height() * .8)))
        self.assertLessEqual((self.pad.frameGeometry().center() - area.center()).manhattanLength(), 24)
        self.assertEqual(self.pad.saved.text(), SAVED_TEXT)
        self.assertGreaterEqual(self.pad.saved.contentsRect().width(),
                                self.pad.saved.fontMetrics().horizontalAdvance(SAVED_TEXT))

    def test_size_slider_updates_font_label_and_settings(self):
        slider = self.pad.size_slider
        self.assertEqual((slider.minimum(), slider.maximum()), (18, 48))
        slider.setValue(48)
        self.assertEqual(self.pad.editor.document().defaultFont().pixelSize(), 48)
        self.assertEqual(self.pad.size_label.text(), "48 px")
        self.assertEqual(self.host.settings["pad"]["size"], 48)

    def test_autofix_at_typed_boundaries_in_maltese_only(self):
        editor = self.pad.editor
        self.pad.switches["autofix"].click()
        QTest.keyClicks(editor, "tieghi habib!")
        self.assertEqual(editor.toPlainText(), "tiegħi ħabib!")
        self.assertEqual(self.pad.word_count.text(), "2 words · kliem")
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), "tiegħi habib")
        self.pad.language_box.setCurrentIndex(1)
        editor.clear()
        QTest.keyClicks(editor, "Bongu ")
        self.assertEqual(editor.toPlainText(), "Bongu ")

    def test_follow_read_speaks_only_the_finished_sentence(self):
        self.pad.switches["followRead"].click()
        editor = self.pad.editor
        editor.setPlainText("😀 Earlier!  ")
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        with patch("ilkelma.app.pad.play_words") as playback:
            QTest.keyClicks(editor, "Second sentence")
            self.assertIsNone(self.host.pending)
            QTest.keyClicks(editor, "?")
            work, finished = self.host.pending
            stop = self.pad.reading
            QTest.keyClicks(editor, " Next")
            self.assertFalse(stop.is_set())
            finished(work())
            self.assertEqual(playback.call_args.args[:2], ("Second sentence?", "mt"))
            self.assertTrue(playback.call_args.kwargs["online"])

    def test_old_pages_list_loads_and_saves_an_archived_page(self):
        self.pad.editor.setPlainText("First page")
        self.pad.new_page()
        self.pad.editor.setPlainText("Current draft")
        self.pad.history_button.click()
        listing = self.pad.history_list
        self.assertEqual(listing.item(0).text(), "First page")
        listing.itemActivated.emit(listing.item(0))
        self.assertEqual(self.pad.editor.toPlainText(), "First page")
        self.assertEqual(self.pad.pages.load(), "First page")
        versions = [path.read_text(encoding="utf-8") for path in self.pad.pages.history.glob("*.txt")]
        self.assertIn("Current draft", versions)

    def test_big_buddy_switch_keeps_mood_and_scales_art(self):
        self.pad.mood("read", "Qed naqra…")
        self.pad.switches["bigBuddy"].click()
        self.assertEqual(self.pad.face.width(), 180)
        self.assertEqual(self.pad.face.pixmap().width(), 180)
        self.assertEqual(self.pad.bubble.text(), "Qed naqra…")
        self.assertTrue(self.host.settings["pad"]["bigBuddy"])
        self.pad.switches["bigBuddy"].click()
        self.assertEqual(self.pad.face.width(), 120)

    def test_fix_flash_and_undo(self):
        original = "Bongu habib tieghi!\nGrazzi hafna."
        self.pad.editor.setPlainText(original)
        self.pad.fix()
        self.assertNotEqual(self.pad.editor.toPlainText(), original)
        green = self.pad.editor.extraSelections()
        self.assertTrue(green)
        self.assertEqual(green[0].format.background().color().name().upper(), MARKS["Green"])
        self.assertTrue(self.pad.flash_timer.isActive())
        self.assertEqual(self.pad.flash_timer.interval(), 2000)
        QTest.keyClick(self.pad.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.pad.editor.toPlainText(), original)
        self.assertEqual(self.pad.editor.extraSelections(), [])

    def test_word_card_and_replacement(self):
        self.pad.editor.setPlainText("Bongu habib!")
        self.pad.editor.setTextCursor(self.pad.selection(2, 2))
        point = self.pad.editor.cursorRect().center()
        QTest.mouseClick(self.pad.editor.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(self.pad.card.isVisible())
        self.assertEqual(self.pad.picked.selectedText(), "Bongu")
        alternatives = [button for button in self.pad.card.findChildren(QPushButton)
                        if button.toolTip() == "Replace word / Ibdel il-kelma"]
        self.assertLessEqual(len(alternatives), 3)
        next(button for button in alternatives if button.text() == "Bonġu").click()
        self.assertEqual(self.pad.editor.toPlainText(), "Bonġu habib!")
        self.assertFalse(self.pad.card.isVisible())

    def test_read_highlights_edge_words_in_selection_then_stops(self):
        whole = "😀 Skip. Kelma kelma!"
        self.pad.editor.setPlainText(whole)
        start = qt_position(whole, whole.index("Kelma"))
        self.pad.editor.setTextCursor(self.pad.selection(start, qt_position(whole, len(whole))))
        self.pad.toggle_read()
        events = [{"text": "Kelma", "offset": 0}, {"text": "kelma", "offset": 4_500_000}]
        seen = []

        def playback(text, language, stop, mark, online=False):
            self.assertEqual(text, "Kelma kelma!")
            self.assertTrue(online)
            for _, a, b in tts.word_timeline(text, events):
                mark(a, b)
                selections = self.pad.editor.extraSelections()
                seen.append(selections[0].cursor.selectedText())

        work, finished = self.host.pending
        with patch("ilkelma.app.pad.play_words", side_effect=playback):
            finished(work())
        self.assertEqual(seen, ["Kelma", "kelma"])
        self.assertIsNone(self.pad.read_span)
        self.pad.toggle_read()
        stop = self.pad.reading
        self.pad.toggle_read()
        self.assertTrue(stop.is_set())

    def test_paste_path_uses_only_control_v(self):
        self.pad.editor.setPlainText("Hello\nChat")
        self.pad.put_in_chat()
        self.host.paste_pad.assert_called_once_with("Hello\nChat")
        host = Mock()
        desktop = host.desktop
        host.pad_target = Target(10, pid=20, focus_point=(30, 40))
        desktop.process.return_value = 20
        desktop.window_at.return_value = 10
        desktop.focused_box.return_value = (30, 40)
        desktop.paste.side_effect = lambda target, text: Desktop.paste(desktop, target, text)
        with patch("ilkelma.engine.windows.put_text") as clipboard, patch(
            "ilkelma.engine.windows.keys", return_value=True
        ) as keys:
            Window.paste_pad(host, "Hello\nChat")
        desktop.paste.assert_called_once_with(host.pad_target, "Hello\nChat")
        clipboard.assert_called_once_with("Hello\nChat", desktop.clipboard_owner)
        keys.assert_called_once_with(17, ord("V"))
        host.pad.mood.assert_called_once_with("done", "Daħal fiċ-chat!", brief=True)
        desktop.paste.reset_mock()
        desktop.focused_box.return_value = (99, 99)
        Window.paste_pad(host, "Do not paste here")
        desktop.paste.assert_not_called()

    def test_repeated_open_keeps_one_window(self):
        host = Mock(pad=None)
        host.open_pad.side_effect = lambda: Window.open_pad(host)
        with patch("ilkelma.app.__main__.Pad") as create:
            create.return_value.isMinimized.return_value = False
            Window.open_pad(host)
            Window.buddy_action(host, "pad")
            create.assert_called_once_with(host)
            self.assertIs(host.pad, create.return_value)

    def test_dictation_and_chosen_help_sentence_insert_at_caret(self):
        self.pad.editor.setPlainText("A B")
        self.pad.editor.setTextCursor(self.pad.selection(2, 2))
        self.pad.dictate("speak")
        mode, language, insert = self.host.pad_capture.call_args.args
        self.assertEqual((mode, language), ("speak", "mt"))
        insert("kelma")
        self.assertEqual(self.pad.editor.toPlainText(), "A kelma B")
        self.pad.dictate("help")
        mode, language, choose = self.host.pad_capture.call_args.args
        self.assertEqual((mode, language), ("help", "mt"))
        choose("idea", [Mock(text="Sentenza.")])
        with patch("ilkelma.app.pad.confirm_phrase") as confirm:
            self.pad.phrase_layout.itemAt(0).widget().click()
        self.assertEqual(self.pad.editor.toPlainText(), "A kelma Sentenza. B")
        confirm.assert_called_once_with("idea", "mt", "Sentenza.")

    def test_autosave_new_page_and_history_limit(self):
        self.pad.editor.setPlainText("First")
        self.pad.autosave.timeout.emit()
        self.assertEqual(self.pad.pages.load(), "First")
        for i in range(25):
            self.pad.editor.setPlainText(f"Version {i}")
            self.pad.autosave.timeout.emit()
        self.pad.editor.setPlainText("Last edit\nSecond line")
        self.pad.new_page()
        self.assertEqual(self.pad.editor.toPlainText(), "")
        self.assertEqual(self.pad.pages.current.read_bytes(), b"")
        versions = sorted(self.pad.pages.history.glob("*.txt"))
        self.assertEqual(len(versions), 20)
        self.assertEqual(versions[-1].read_bytes(), b"Last edit\nSecond line")


class EdgeBoundaryTests(unittest.TestCase):
    def test_collect_audio_requests_and_preserves_word_boundaries(self):
        event = {"type": "WordBoundary", "text": "Kelma", "offset": 1_000_000, "duration": 4_000_000}

        async def stream():
            yield event
            yield {"type": "audio", "data": b"mp3"}

        with patch.object(tts.edge_tts, "Communicate") as communicate:
            communicate.return_value.stream.side_effect = stream
            audio, cues = asyncio.run(tts.collect_audio("Kelma", "voice", timed=True))
        self.assertEqual(communicate.call_args.kwargs["boundary"], "WordBoundary")
        self.assertEqual(audio, b"mp3")
        self.assertEqual(list(tts.word_timeline("Kelma", cues)), [(0.1, 0, 5)])

    def test_windows_word_status_counts_utf16_and_suppresses_duplicates(self):
        class Voice:
            WaitUntilDone = Mock(side_effect=[False, False, False, True])
            statuses = iter((Mock(InputWordPosition=3, InputWordLength=5),
                             Mock(InputWordPosition=3, InputWordLength=5),
                             Mock(InputWordPosition=9, InputWordLength=4)))

            @property
            def Status(self):
                return next(self.statuses)

        text = "😀 Kelma oħra"
        spans = []
        tts.follow_sapi(Voice(), text, Event(), lambda a, b: spans.append(text[a:b]))
        self.assertEqual(spans, ["Kelma", "oħra"])
