import ast
import os
import math
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

import win32con
import win32gui
from PySide6.QtCore import QPoint, Qt
from PySide6.QtQml import QJSEngine

from ilkelma.app import __main__ as app, storage
from ilkelma.app.buddy import Buddy, feedback, mood_for, reaction
from ilkelma.app.buddy_native import GetWindowLongPtr
from ilkelma.engine.target import Target
from ilkelma.engine.windows import Desktop, ForegroundTracker, USER
from ilkelma.languages import packs


class BuddyFeedbackTests(unittest.TestCase):
    def test_real_activity_and_temporary_reactions(self):
        expected = {"idle": "rest", "listening": "listen", "busy": "think",
                    "preview": "idea", "reading": "read"}
        self.assertEqual({state: mood_for(state, True) for state in expected}, expected)
        self.assertEqual(mood_for("idle", False), "sleep")
        self.assertEqual(mood_for("reading", False), "read")
        for text in ("Grazzi!", "Thank you.", "grazie mille"):
            self.assertEqual(reaction(text), "thanks")
        for text in ("Ħaħa!", "Ha ha, haha!", "LOL"):
            self.assertEqual(reaction(text), "laugh")
        self.assertIsNone(reaction("lollipop"))
        buddy = Mock()
        Buddy.show_message(buddy, "Your text is in!", "done", 4000)
        buddy.message_timer.start.assert_called_once_with(6000)
        buddy.set_reaction.assert_called_once_with("done", 4000)
        Buddy.end_mood(buddy)
        self.assertEqual(buddy.temporary_mood, "")
        Buddy.dismiss(buddy)
        self.assertEqual(buddy.message, "")
        Buddy.show_message(buddy, "long " * 35, "oops")
        self.assertGreater(buddy.message_timer.start.call_args.args[0], 6000)

    def test_feedback_keys_have_matching_localized_bubbles(self):
        entries = packs()["en"]["buddy_feedback"]
        for code, pack in packs().items():
            self.assertEqual(set(pack["buddy"]), set(entries))
            self.assertEqual(feedback(code, "select_text")[1:], ("oops", True))
            self.assertEqual(feedback(code, "no_mic")[1], "sleep")
            self.assertEqual(feedback(code, "voices_failed")[1], "sad")
            self.assertEqual(feedback(code, "phrase_saved")[1], "party")
        self.assertEqual(feedback("mt", "greeting", user="Clifford", buddy="Kelma")[0],
                         "Bonġu Clifford! Jien Kelma.")
        source = Path(app.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"ask", "bubble"} and node.args
                    and isinstance(node.args[0], ast.Constant)):
                self.assertIn(node.args[0].value, entries)

    def test_no_popup_feedback_in_production(self):
        root = Path(app.__file__).parents[1]
        forbidden = re.compile(r"QMessageBox|MessageBox[AW]?\s*\(|\.showMessage\s*\(")
        for path in root.rglob("*.py"):
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), str(path))


class BuddySettingsTests(unittest.TestCase):
    def test_names_persist_in_settings_as_utf8(self):
        self.assertEqual(storage.DEFAULTS["buddy_name"], "Kelma")
        self.assertTrue(storage.DEFAULTS["buddy_speaks"])
        window = Mock(settings=storage.DEFAULTS.copy())
        window.change.side_effect = lambda key, value: app.Window.change(window, key, value)
        with tempfile.TemporaryDirectory(dir=storage.DATA.parent) as folder:
            with patch.object(storage, "DATA", Path(folder)):
                app.Window.save_name(window, Mock(**{"text.return_value": " Clifford "}), "user_name")
                app.Window.save_name(window, Mock(**{"text.return_value": "Ġiġi"}), "buddy_name")
                saved = storage.load("settings", {})
                self.assertEqual((saved["user_name"], saved["buddy_name"]), ("Clifford", "Ġiġi"))
                self.assertEqual(window.buddy.name, "Ġiġi")
                self.assertIn("Ġiġi".encode(), (Path(folder) / "settings.json").read_bytes())

    def test_language_cycle_updates_real_setting_and_bubble(self):
        window = Mock(settings={"language": "mt"})
        window.language.findData.side_effect = lambda code: code
        window.change.side_effect = window.settings.__setitem__

        def select(code):
            window.language.currentData.return_value = code
            app.Window.language_changed(window)

        window.language.setCurrentIndex.side_effect = select
        for code in ("en", "it", "mt"):
            app.Window.cycle_buddy_language(window)
            self.assertEqual(window.settings["language"], code)
            self.assertEqual(window.buddy.language, code)
            window.change.assert_called_with("language", code)
            window.bubble.assert_called_with("language")

    def test_clip_and_unclip_persist_and_restore_box_focus(self):
        window = Mock(settings={}, recorder=None, clipped=None, pending_phrase=None)
        window.change.side_effect = window.settings.__setitem__
        window.desktop.process.return_value = 77
        point = (140, 180)
        target = Target(20, point=point, pid=77)
        saved = {"process": "browser.exe", "title": "Chat", "point": [40, 80]}
        window.buddy_target.return_value = Target(20, pid=77)
        window.desktop.focused_box.return_value = point
        window.desktop.window_at.return_value = 20
        window.desktop.save_clip.return_value = saved
        window.bind_clip.side_effect = lambda value: app.Window.bind_clip(window, value)
        with patch.object(app.win32gui, "GetWindowRect", return_value=(100, 100, 300, 300)):
            app.Window.toggle_clip(window)
            self.assertTrue(window.buddy.clipped)
            self.assertEqual(window.settings["clip"], saved)
            self.assertEqual(window.clipped, target)
            app.Window.buddy_action(window, "write")
            window.toggle.assert_called_once_with(Target(20, pid=77, focus_point=point))
            window.desktop.restore_focus.assert_called_once_with(point)
            window.buddy.anchor = QPoint(200, 150)
            app.Window.toggle_clip(window)
            self.assertIsNone(window.settings["clip"])
            self.assertIsNone(window.clipped)
            self.assertFalse(window.buddy.clipped)
            window.save_buddy_position.assert_called_once_with(window.buddy.anchor)

    def test_saved_clip_is_restored_without_changing_settings(self):
        window = Mock(settings={"clip": {"point": [12, 34]}}, clipped=None)
        target = Target(20, (12, 34), 77)
        window.desktop.restore_clip.return_value = target
        app.Window.track_clip(window)
        window.desktop.restore_clip.assert_called_once_with(window.settings["clip"])
        window.bind_clip.assert_called_once_with(target)
        window.change.assert_not_called()

    def test_invalid_saved_clip_starts_unclipped(self):
        window = Mock(settings={"clip": {"process": "explorer.exe"}}, clipped=None)
        window.buddy.clipped = False
        window.desktop.restore_clip.return_value = None
        app.Window.track_clip(window, startup=True)
        window.change.assert_called_once_with("clip", None)
        window.bind_clip.assert_not_called()
        self.assertFalse(window.buddy.clipped)

    def test_read_buttons_use_last_window_without_a_typing_box(self):
        window = Mock(clipped=Target(30, (15, 25), 88), recorder=None, pending_phrase=None)
        target = Target(20, pid=77)
        window.foreground_tracker.current.return_value = target
        window.desktop.process.return_value = 77
        window.desktop.focused_box.return_value = None
        window.buddy_target.side_effect = lambda: app.Window.buddy_target(window)
        app.Window.buddy_action(window, "read")
        window.desktop.focused_box.assert_not_called()
        app.Window.buddy_action(window, "last")
        window.read_selection.assert_called_once_with(target)
        window.read_last.assert_called_once_with(target)
        window.desktop.activate.assert_called_with(20)
        window.bubble.assert_not_called()

        window.desktop.is_foreground.return_value = False
        app.Window.buddy_action(window, "read")
        window.read_selection.assert_called_once()
        window.bubble.assert_called_once_with("select_text")

    def test_clipped_buddy_cannot_start_drag(self):
        buddy = Mock(clipped=True)
        Buddy.command(buddy, "drag")
        buddy.follow.start.assert_not_called()


class BuddyWindowTests(unittest.TestCase):
    def test_overlay_uses_non_activating_window_flags(self):
        application = app.QApplication.instance() or app.QApplication([])
        # Inspect the actual widget without showing it or starting Il-Kelma.
        with patch.object(Buddy, "load"):
            buddy = Buddy("Kelma", "mt")
        try:
            flags = buddy.windowFlags()
            for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.Tool,
                         Qt.WindowType.WindowStaysOnTopHint, Qt.WindowType.WindowDoesNotAcceptFocus):
                self.assertTrue(flags & flag)
            self.assertTrue(buddy.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
            self.assertTrue(buddy.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertEqual(buddy.focusPolicy(), Qt.FocusPolicy.NoFocus)
            hwnd = int(buddy.winId())
            buddy.protect_native_windows()
            child = win32gui.CreateWindow("Static", "", win32con.WS_CHILD,
                                          0, 0, 12, 12, hwnd, 0, 0, None)
            try:
                for window in (hwnd, child):
                    self.assertTrue(GetWindowLongPtr(window, win32con.GWL_EXSTYLE)
                                    & win32con.WS_EX_NOACTIVATE)
                    self.assertEqual(win32gui.SendMessage(window, win32con.WM_MOUSEACTIVATE, hwnd, 0),
                                     win32con.MA_NOACTIVATE)
            finally:
                win32gui.DestroyWindow(child)
        finally:
            buddy.deleteLater()
            application.processEvents()

    def test_seven_round_buttons_fit_without_overlap_at_screen_edges(self):
        application = app.QApplication.instance() or app.QApplication([])
        engine = QJSEngine()
        source = Path(app.__file__).with_name("buddy.js").read_text(encoding="utf-8")
        html = Path(app.__file__).with_name("buddy.html").read_text(encoding="utf-8")
        round_buttons = re.findall(r'<button class="rb" id="([^"]+)"', html)
        self.assertEqual(len(round_buttons), 7)
        self.assertEqual(round_buttons[-1], "b-comments")
        layout = source[source.index("const clamp"):source.index("function render")]
        self.assertFalse(engine.evaluate(layout).isError())
        for width, height in ((1920, 1080), (2560, 1440), (640, 480), (360, 330), (360, 230)):
            area = app.QRect(0, 0, width, height)
            xs = (0, width // 2 - 52, width - 105)
            ys = (0, height // 2 - 52, height - 123)
            for x in xs:
                for y in ys:
                    with self.subTest(width=width, height=height, x=x, y=y):
                        buddy = Mock(open=True, context=False, loaded=True,
                                     anchor=QPoint(x, y), message="", base_mood="rest",
                                     temporary_mood="", language="mt", clipped=False, comments_active=False,
                                     listen_active=False,
                                     comments_reading=False)
                        buddy.name = "Kelma"
                        buddy.screen_area.return_value = area
                        buddy.visible_position.return_value = buddy.anchor
                        Buddy.refresh(buddy)
                        bounds = buddy.setGeometry.call_args.args[0]
                        render = buddy.page.return_value.runJavaScript.call_args.args[0]
                        state = render.removeprefix("render(").removesuffix(")")
                        result = engine.evaluate(
                            f"buttonPositions({state}, {bounds.width()}, {bounds.height()})")
                        self.assertFalse(result.isError(), result.toString())
                        positions = result.toVariant()
                        self.assertEqual(len(positions), 7)
                        cx, cy = x - bounds.x() + 52, y - bounds.y() + 52
                        centers = [(left + 25, top + 25) for left, top in positions]
                        rotated = height == 330 and y == ys[1] and x in (xs[0], xs[-1])
                        for index, (left, top) in enumerate(positions):
                            self.assertGreaterEqual(left, 5 - 1e-8)
                            self.assertGreaterEqual(top, 5 - 1e-8)
                            self.assertLessEqual(left + 55, bounds.width() + 1e-8)
                            self.assertLessEqual(top + 55, bounds.height() + 1e-8)
                            center = centers[index]
                            radius = math.dist(center, (cx, cy))
                            self.assertLessEqual(radius, 200 if index < 6 else 225)
                            if height >= 480 or rotated:
                                self.assertAlmostEqual(radius, 168 if index < 6 else 106)
                            for other in centers[:index]:
                                self.assertGreaterEqual(math.dist(center, other), 52)
                        if height >= 480 or rotated:
                            self.assertAlmostEqual(math.dist(centers[0], centers[5]), 168 * math.sqrt(2))
                            for first, second in zip(centers[:5], centers[1:6]):
                                self.assertAlmostEqual(math.dist(first, second),
                                                       336 * math.sin(math.pi / 20))
                            if x in (xs[0], xs[-1]):
                                inward = 1 if x == xs[0] else -1
                                self.assertTrue(all(inward * (px - cx) >= -1e-8 for px, _ in centers))
                            if y in (ys[0], ys[-1]):
                                inward = 1 if y == ys[0] else -1
                                self.assertTrue(all(inward * (py - cy) >= -1e-8 for _, py in centers))
        application.processEvents()


class ForegroundTrackerTests(unittest.TestCase):
    def test_foreground_hook_keeps_last_real_window(self):
        desktop = Desktop()
        processes = {1: 77, 2: os.getpid(), 3: 99, 4: 99, 5: 99, 6: 99, 7: 99, 8: 99}
        classes = {3: "CabinetWClass", 4: "Shell_TrayWnd", 5: "Shell_SecondaryTrayWnd",
                   6: "Progman", 7: "WorkerW"}
        with (patch.object(USER, "SetWinEventHook", return_value=40),
              patch.object(USER, "UnhookWinEvent") as unhook,
              patch.object(desktop, "foreground", return_value=1) as foreground,
              patch.object(desktop, "root", side_effect=lambda hwnd: hwnd),
              patch.object(desktop, "process", side_effect=processes.__getitem__),
              patch.object(desktop, "exists", side_effect=processes.__contains__),
              patch.object(win32gui, "GetDesktopWindow", return_value=8),
              patch.object(win32gui, "GetClassName", side_effect=lambda hwnd: classes.get(hwnd, "App")),
              patch.object(desktop, "window_identity", side_effect=lambda hwnd:
                           ("explorer.exe" if hwnd == 3 else "app.exe", ""))):
            tracker = ForegroundTracker(desktop)
            for hwnd in (0, 2, 3, 4, 5, 6, 7, 8):
                foreground.return_value = hwnd
                tracker.callback(40, 3, hwnd, 0, 0, 0, 0)
                self.assertEqual(tracker.current(), Target(1, pid=77))
            processes[1] = 78
            self.assertIsNone(tracker.current())
            tracker.close()
            unhook.assert_called_once_with(40)

    def test_explorer_clip_is_rejected_without_looking_for_a_window(self):
        with patch.object(win32gui, "EnumWindows") as enumerate_windows:
            saved = {"process": "explorer.exe", "title": "", "point": [10, 10]}
            self.assertIsNone(Desktop().restore_clip(saved))
            enumerate_windows.assert_not_called()
