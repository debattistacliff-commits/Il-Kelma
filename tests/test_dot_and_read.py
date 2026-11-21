import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect
import uiautomation as uia

from ilkelma.app.__main__ import Window, buddy_position
from ilkelma.app.hotkeys import Hotkeys, NO_REPEAT, READ_FALLBACKS, bind_startup
from ilkelma.engine import windows
from ilkelma.engine.target import Target
from ilkelma.engine.tidy import collapse_repeats


class HotkeyTests(unittest.TestCase):
    def test_read_fallback_order_and_control_alt_modifiers(self):
        with patch("ilkelma.app.hotkeys.USER") as user:
            user.RegisterHotKey.side_effect = [False, False, True]
            keys = Hotkeys(Mock(**{"winId.return_value": 20}), {})
            with self.assertLogs(level="INFO") as logs:
                self.assertEqual(bind_startup(keys, 2, "F10", READ_FALLBACKS), "Ctrl+Alt+R")
            self.assertEqual([call.args[-1] for call in user.RegisterHotKey.call_args_list],
                             [121, 119, ord("R")])
            user.RegisterHotKey.assert_called_with(20, 2, NO_REPEAT | 3, ord("R"))
            self.assertIn("F10", logs.output[0])
            self.assertIn("F8", logs.output[1])
            self.assertIn("registered", logs.output[2])

    def test_settings_show_the_active_fallback_and_notice_names_it(self):
        window = Mock(settings={"write_key": "F9", "read_key": "F10"})
        window.hotkeys.bind.side_effect = lambda slot, key: key != "F10"
        message = Window.register_shortcuts(window)
        self.assertEqual(message, "F10 could not be registered. Use F8 for Read.")
        window.hotkeys.bound = {1: "F9", 2: "F8"}
        window.key_boxes = {2: Mock()}
        Window.show_active_key(window, 2)
        window.key_boxes[2].setCurrentText.assert_called_once_with("F8")


class ReadingTests(unittest.TestCase):
    def test_selection_uses_text_pattern_on_parent_without_copying(self):
        desktop = windows.Desktop()
        item = Mock(**{"GetText.return_value": "Bonġu, ħabib."})
        parent = Mock(IsPassword=False, **{"GetPattern.return_value.GetSelection.return_value": [item]})
        focused = Mock(IsPassword=False, NativeWindowHandle=0,
                       **{"GetPattern.return_value": None, "GetParentControl.return_value": parent})
        with (patch.object(uia, "UIAutomationInitializerInThread"),
              patch.object(uia, "GetFocusedControl", return_value=focused),
              patch.object(desktop, "activate", return_value=True),
              patch.object(desktop, "is_foreground", return_value=True),
              patch.object(desktop, "copy_selection") as copy):
            self.assertEqual(desktop.selection(Mock(hwnd=20)), "Bonġu, ħabib.")
            parent.GetPattern.assert_called_once_with(uia.PatternId.TextPattern)
            copy.assert_not_called()

    def test_copy_fallback_and_password_guard(self):
        desktop = windows.Desktop()
        target = Mock()
        with (patch.object(desktop, "activate", return_value=True),
              patch.object(desktop, "automation_selection", side_effect=["", None]),
              patch.object(desktop, "copy_selection", return_value="Copied text") as copy):
            self.assertEqual(desktop.selection(target), "Copied text")
            self.assertEqual(desktop.selection(target), "")
            copy.assert_called_once_with(target)

    def test_message_above_the_composer_in_chat_fixture(self):
        html = Path(__file__).with_name("pages").joinpath("chatbox.html").read_text(encoding="utf-8")
        messages = re.findall(r"<p>(.*?)</p>", html)
        self.assertGreaterEqual(len(messages), 3)
        types = uia.ControlType

        def control(kind, bounds, name="", children=(), role="", offscreen=False):
            return SimpleNamespace(ControlType=kind, AriaRole=role, IsPassword=False,
                                   IsOffscreen=offscreen, BoundingRectangle=uia.Rect(*bounds),
                                   Name=name, GetChildren=lambda: children)

        text = [control(types.TextControl, (50, y, 210, y + 20), message)
                for y, message in zip(range(100, 200, 25), messages)]
        hidden = control(types.TextControl, (50, 240, 210, 260), "Hidden message", offscreen=True)
        log = control(types.GroupControl, (40, 90, 350, 270), children=[*text, hidden], role="log")
        sidebar = control(types.TextControl, (0, 270, 30, 290), "Other conversation")
        toolbar = control(types.ToolBarControl, (50, 270, 240, 290), children=[sidebar])
        editor = control(types.EditControl, (45, 300, 350, 330), "Draft text")
        page = control(types.DocumentControl, (0, 30, 410, 340), children=[log, sidebar, toolbar, editor])
        with patch.object(uia, "ControlsAreSame", side_effect=lambda a, b: a is b):
            self.assertEqual(windows.Desktop.message_above(uia, page, editor),
                             "Narak għada, ħabib tiegħi!")


class ClipTests(unittest.TestCase):
    def test_process_name_uses_the_windows_process_image(self):
        desktop = windows.Desktop()
        with (patch.object(desktop, "process", return_value=os.getpid()),
              patch.object(desktop, "title", return_value="Chat app")):
            self.assertEqual(desktop.window_identity(20), (Path(sys.executable).name.lower(), "Chat app"))

    def test_save_and_restore_by_identity_and_relative_point(self):
        desktop = windows.Desktop()
        identity = ("browser.exe", "Chat app")
        with (patch.object(windows, "win32gui") as gui,
              patch.object(desktop, "window_identity", return_value=identity) as identify,
              patch.object(desktop, "window_at", return_value=30),
              patch.object(desktop, "process", return_value=77),
              patch.object(desktop, "box_bounds", return_value=(110, 210, 140, 240))):
            gui.GetWindowRect.return_value = (100, 200, 600, 650)
            saved = desktop.save_clip(Target(20, (120, 225), 77))
            self.assertEqual(saved, {"process": "browser.exe", "title": "Chat app", "point": [20, 25]})
            gui.EnumWindows.side_effect = lambda callback, arg: callback(30, arg)
            gui.GetWindowText.return_value = "Chat app"
            gui.IsWindowVisible.return_value = True
            gui.IsIconic.return_value = False
            gui.GetWindowRect.return_value = (200, 300, 700, 750)
            self.assertEqual(desktop.restore_clip(saved), Target(30, (220, 325), 77))
            identify.return_value = ("chatapp.exe", "Chat app")
            self.assertIsNone(desktop.restore_clip(saved))

    def test_buddy_sits_to_the_right_or_above(self):
        area = QRect(0, 0, 800, 600)
        box = QRect(100, 400, 400, 40)
        right = buddy_position(box, area, 58, 58)
        self.assertGreater(right.x(), box.right())
        box.setRight(780)
        above = buddy_position(box, area, 58, 58)
        self.assertLess(above.y() + 58, box.top())



class RepeatTests(unittest.TestCase):
    def test_consecutive_exact_phrases(self):
        for text, expected in (
            ("Għandek raġun ħabib tiegħi għandek raġun ħabib tiegħi għandek raġun ħabib tiegħi.",
             "Għandek raġun ħabib tiegħi."),
            ("Narak għada żgur narak għada żgur!", "Narak għada żgur!"),
            ("I agree with you. I agree with you.", "I agree with you."),
            ("Kollox sew kollox sew kollox sew kollox sew", "Kollox sew kollox sew kollox sew kollox sew"),
            ("Narak għada żgur narak illum żgur", "Narak għada żgur narak illum żgur"),
            ("Narak għada żgur u narak għada żgur", "Narak għada żgur u narak għada żgur"),
        ):
            with self.subTest(text=text):
                self.assertEqual(collapse_repeats(text), expected)
