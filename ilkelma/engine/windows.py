import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import PureWindowsPath
import time

import pythoncom
import win32api
import win32clipboard as clipboard
import win32con as con
import win32gui
import win32process

VK_F24 = 0x87  # harmless key; used to let Windows hand us the foreground

from .target import Target, remember

USER = ctypes.windll.user32


FOREGROUND_EVENT = 3
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
    wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)
USER.SetWinEventHook.argtypes = (
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WinEventProc,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
)
USER.SetWinEventHook.restype = wintypes.HANDLE
USER.UnhookWinEvent.argtypes = (wintypes.HANDLE,)


class ForegroundTracker:
    """Keep the last external app even when the foreground goes temporarily blank."""

    def __init__(self, desktop):
        self.desktop = desktop
        self.target = None
        self.callback = WinEventProc(self.changed)
        # Out-of-context events arrive on the Qt thread that installs the hook.
        self.hook = USER.SetWinEventHook(FOREGROUND_EVENT, FOREGROUND_EVENT, None,
                                         self.callback, 0, 0, 0)
        if not self.hook:
            logging.warning("Could not install the foreground event hook")
        self.observe(desktop.foreground())

    def changed(self, hook, event, hwnd, object_id, child_id, thread, time_ms):
        self.observe(hwnd)

    def observe(self, hwnd):
        hwnd = self.desktop.root(hwnd)
        if pid := self.desktop.user_window_pid(hwnd):
            self.target = Target(hwnd, pid=pid)

    def current(self):
        self.observe(self.desktop.foreground())
        if self.target and self.desktop.user_window_pid(self.target.hwnd) == self.target.pid:
            return self.target
        return None

    def close(self):
        if self.hook:
            USER.UnhookWinEvent(self.hook)
            self.hook = None


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class KeyInput(ctypes.Structure):
    _fields_ = [("key", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class InputData(ctypes.Union):
    _fields_ = [("mouse", MouseInput), ("key", KeyInput)]


class Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("data", InputData)]


def keys(*codes):
    events = [Input(1, InputData(key=KeyInput(code))) for code in codes]
    events.extend(Input(1, InputData(key=KeyInput(code, 0, con.KEYEVENTF_KEYUP)))
                  for code in reversed(codes))
    return USER.SendInput(len(events), (Input * len(events))(*events), ctypes.sizeof(Input)) == len(events)


def clipboard_call(operation, owner=None):
    for attempt in range(12):
        try:
            clipboard.OpenClipboard(owner)
            break
        except clipboard.error:
            if attempt == 11:
                raise
            time.sleep(0.025)
    try:
        return operation()
    finally:
        clipboard.CloseClipboard()


def put_text(text, owner):
    def write():
        clipboard.EmptyClipboard()
        clipboard.SetClipboardText(text, con.CF_UNICODETEXT)

    clipboard_call(write, owner)


def screen_origins():
    monitors = (win32api.GetMonitorInfo(handle) for handle, _, _ in win32api.EnumDisplayMonitors())
    return {monitor["Device"]: monitor["Monitor"][:2] for monitor in monitors}


class Desktop:
    def __init__(self, clipboard_owner=None):
        self.clipboard_owner = clipboard_owner

    def describe(self, hwnd):
        if self.exists(hwnd):
            try:
                return f"hwnd={hwnd:#x} title={win32gui.GetWindowText(hwnd)!r} pid={self.process(hwnd)}"
            except win32gui.error:
                pass
        return f"hwnd={hwnd or 0:#x} unavailable"

    def foreground(self):
        return win32gui.GetForegroundWindow()

    def exists(self, hwnd):
        return win32gui.IsWindow(hwnd)

    def process(self, hwnd):
        return win32process.GetWindowThreadProcessId(hwnd)[1]

    def title(self, hwnd):
        return win32gui.GetWindowText(hwnd) if self.exists(hwnd) else ""

    def window_at(self, point):
        hwnd = win32gui.WindowFromPoint(point)
        return win32gui.GetAncestor(hwnd, con.GA_ROOT) if hwnd else 0

    def window_identity(self, hwnd):
        # PROCESS_QUERY_LIMITED_INFORMATION works without requesting access to memory.
        path = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(path))
        query = ctypes.windll.kernel32.QueryFullProcessImageNameW
        query.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                          ctypes.POINTER(wintypes.DWORD))
        handle = win32api.OpenProcess(0x1000, False, self.process(hwnd))
        try:
            if not query(int(handle), 0, path, ctypes.byref(length)):
                raise ctypes.WinError()
        finally:
            handle.Close()
        return PureWindowsPath(path.value).name.lower(), self.title(hwnd)

    def user_window_pid(self, hwnd):
        try:
            if (not hwnd or not self.exists(hwnd) or hwnd == win32gui.GetDesktopWindow()
                    or win32gui.GetClassName(hwnd) in SHELL_CLASSES):
                return None
            pid = self.process(hwnd)
            if pid and pid != os.getpid() and self.window_identity(hwnd)[0] != "explorer.exe":
                return pid
        except (OSError, win32gui.error):
            pass
        return None

    def comments_window(self, hwnd):
        try:
            if self.user_window_pid(hwnd) and not win32gui.IsIconic(hwnd):
                process, title = self.window_identity(hwnd)
                return process, title, win32gui.GetWindowRect(hwnd)
        except (OSError, win32gui.error):
            logging.debug("Could not identify the comments window", exc_info=True)
        return None

    def save_clip(self, target):
        if not target.point or not self.user_window_pid(target.hwnd) or not target.valid(self):
            return None
        try:
            process, title = self.window_identity(target.hwnd)
            left, top, _, _ = win32gui.GetWindowRect(target.hwnd)
            x, y = target.point
            return {"process": process, "title": title, "point": [x - left, y - top]}
        except (OSError, win32gui.error):
            logging.exception("Could not remember the clipped window")
            return None

    def restore_clip(self, saved):
        if not saved:
            return None
        try:
            identity = saved["process"], saved["title"]
            if (not all(isinstance(value, str) for value in identity)
                    or identity[0].lower() == "explorer.exe"):
                return None
            x, y = saved["point"]
            if not isinstance(x, int) or not isinstance(y, int):
                return None
        except (KeyError, TypeError, ValueError):
            return None
        matches = []

        def check(hwnd, _):
            try:
                if (not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd)
                        or win32gui.GetWindowText(hwnd) != identity[1]
                        or not self.user_window_pid(hwnd)
                        or self.window_identity(hwnd) != identity):
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                point = left + x, top + y
                if 0 <= x < right - left and 0 <= y < bottom - top and self.window_at(point) == hwnd:
                    target = self.remember(point)
                    if self.box_bounds(target):
                        matches.append(target)
            except (OSError, win32gui.error):
                logging.debug("Could not inspect window %s for the saved clip", hwnd, exc_info=True)

        win32gui.EnumWindows(check, None)
        return matches[0] if len(matches) == 1 else None

    def root(self, hwnd):
        """Top-level window. Some apps give focus to inner child windows."""
        try:
            return win32gui.GetAncestor(hwnd, con.GA_ROOT) or hwnd if hwnd else 0
        except win32gui.error:
            return hwnd

    def is_foreground(self, hwnd):
        return bool(hwnd) and self.root(self.foreground()) == self.root(hwnd)

    def activate(self, hwnd):
        if self.is_foreground(hwnd):
            logging.info("Activate already foreground: success")
            return True
        if win32gui.IsIconic(hwnd):
            logging.info("Activate restoring minimized window")
            win32gui.ShowWindow(hwnd, con.SW_RESTORE)
        for method, action in (("SetForegroundWindow", lambda: win32gui.SetForegroundWindow(hwnd)),
                               ("F24 then SetForegroundWindow", lambda: self.activate_with_alt(hwnd)),
                               ("AttachThreadInput", lambda: self.activate_attached(hwnd))):
            try:
                action()
            except (win32gui.error, OSError) as error:
                logging.info("Activate %s raised %s", method, error)
            foreground = self.foreground()
            success = self.is_foreground(hwnd)
            logging.info("Activate %s: success=%s foreground=%s",
                         method, success, self.describe(foreground))
            if success:
                return True
        return False

    def activate_with_alt(self, hwnd):
        # A key event lets Windows hand us the foreground. Not Alt: Alt opens the menu in
        # some browsers and text editors, which then swallow Ctrl+C / Ctrl+V. F24 does nothing.
        win32api.keybd_event(VK_F24, 0, 0, 0)
        win32api.keybd_event(VK_F24, 0, con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)

    def activate_attached(self, hwnd):
        foreground = self.foreground()
        if not foreground:
            return
        active_thread = win32process.GetWindowThreadProcessId(foreground)[0]
        current_thread = win32api.GetCurrentThreadId()
        attached = active_thread != current_thread
        if attached:
            win32process.AttachThreadInput(current_thread, active_thread, True)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current_thread, active_thread, False)

    def focused_box(self, *, editor_only=False):
        """Remember where the typing cursor is (e.g. a chat input) when recording starts."""
        try:
            import uiautomation as uia

            with uia.UIAutomationInitializerInThread():
                control = None
                for attempt in range(4):  # Windows sometimes refuses the first ask; try again briefly
                    try:
                        control = uia.GetFocusedControl()
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                        import time
                        time.sleep(0.08)
                if not control or control.IsPassword:
                    return None
                rect = control.BoundingRectangle
                if rect.width() <= 0 or rect.height() <= 0:
                    return None
                if editor_only:
                    edit = control.ControlType == uia.ControlType.EditControl or control.AriaRole == "textbox"
                    # Some web editors expose the input as a focusable group.
                    group = (control.ControlType == uia.ControlType.GroupControl
                             and control.IsKeyboardFocusable and rect.height() < 400)
                    if not (edit or group):
                        return None
                    value = control.GetPattern(uia.PatternId.ValuePattern)
                    if value and value.IsReadOnly:
                        return None
                point = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                if not editor_only:
                    logging.info("Remembered text box %s at %s", control.ControlTypeName, point)
                return point
        except Exception:
            logging.exception("Could not remember the text box")
            return None

    def restore_focus(self, point):
        """Put the cursor back in the remembered box without clicking (a click would move the caret)."""
        try:
            import uiautomation as uia

            with uia.UIAutomationInitializerInThread():
                control = uia.ControlFromPoint(*point)
                if control and not control.HasKeyboardFocus:
                    control.SetFocus()
                    logging.info("Restored focus to %s", control.ControlTypeName)
        except Exception:
            logging.exception("Could not restore the text box focus")

    def focus_at(self, point):
        try:
            import uiautomation as uia

            with uia.UIAutomationInitializerInThread():
                element = uia.ControlFromPoint(*point)
                if element and element.ControlType in (uia.ControlType.EditControl,
                                                        uia.ControlType.DocumentControl):
                    element.SetFocus()
        except (OSError, RuntimeError, AttributeError):
            pass
        if self.window_at(point) == self.foreground():
            position = win32api.GetCursorPos()
            win32api.SetCursorPos(point)
            try:
                win32api.mouse_event(con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                win32api.mouse_event(con.MOUSEEVENTF_LEFTUP, 0, 0)
            finally:
                win32api.SetCursorPos(position)

    def remember(self, point=None):
        return remember(self, point)

    def paste(self, target, text):
        logging.info("Paste target: %s", self.describe(target.hwnd))
        logging.info("Foreground before paste: %s", self.describe(self.foreground()))
        put_text(text, self.clipboard_owner)
        logging.info("Paste text placed on clipboard")
        focused = target.focus(self)
        logging.info("Paste focus result=%s foreground=%s", focused, self.describe(self.foreground()))
        if not focused or not self.is_foreground(target.hwnd):
            return False
        sent = keys(con.VK_CONTROL, ord("V"))
        logging.info("Paste Ctrl+V sent=%s", sent)
        return sent

    def automation_selection(self, target):
        import uiautomation as uia

        with uia.UIAutomationInitializerInThread():
            if not self.is_foreground(target.hwnd):
                return ""
            control = uia.GetFocusedControl()
            while control:
                if control.IsPassword:
                    return None
                pattern = control.GetPattern(uia.PatternId.TextPattern)
                if pattern:
                    text = " ".join(item.GetText(6000) for item in pattern.GetSelection()).strip()
                    if text:
                        return text
                if control.NativeWindowHandle == target.hwnd:
                    break
                control = control.GetParentControl()
        return ""

    def restore_clipboard(self, previous, copied):
        """Give the user's old clipboard back. Never let this stop the reading."""
        for _attempt in range(10):
            if USER.GetClipboardSequenceNumber() != copied:
                return  # something else changed the clipboard meanwhile; leave it alone
            try:
                pythoncom.OleSetClipboard(previous)
                pythoncom.OleFlushClipboard()
                return
            except Exception:
                time.sleep(0.05)
        logging.warning("Read: could not restore the previous clipboard")

    def selection(self, target):
        if not target.valid(self) or not self.activate(target.hwnd):
            return ""
        try:
            selected = self.automation_selection(target)
            if selected is None or selected:
                return selected or ""
        except Exception:
            logging.exception("Could not read the UI Automation selection")
        return self.copy_selection(target)

    def copy_selection(self, target):
        ole = ctypes.windll.ole32
        result = ole.OleInitialize(None)
        if result < 0:
            raise OSError(f"OleInitialize failed ({result:#x})")
        try:
            previous = pythoncom.OleGetClipboard()
            time.sleep(0.15)  # let the user's hotkey/mouse button come up first
            if not self.is_foreground(target.hwnd):
                return ""
            before = USER.GetClipboardSequenceNumber()
            if not keys(con.VK_CONTROL, ord("C")):
                logging.info("Read: Ctrl+C could not be sent")
                return ""
            logging.info("Read: Ctrl+C sent to %s", self.describe(self.foreground()))
            for _ in range(75):  # up to 1.5 s; browsers can be slow to fill the clipboard
                time.sleep(0.02)
                copied = USER.GetClipboardSequenceNumber()
                if copied != before:
                    text = ""
                    for _attempt in range(10):  # the browser may still hold the clipboard open
                        try:
                            text = clipboard_call(
                                lambda: clipboard.GetClipboardData(con.CF_UNICODETEXT)
                                if clipboard.IsClipboardFormatAvailable(con.CF_UNICODETEXT) else "")
                            break
                        except Exception:
                            time.sleep(0.05)
                    logging.info("Read: copied %d characters", len(text or ""))
                    self.restore_clipboard(previous, copied)
                    return text
            return ""
        finally:
            ole.OleUninitialize()

    def editor_at(self, uia, target):
        control = uia.ControlFromPoint(*target.point)
        while control:
            if control.ControlType == uia.ControlType.EditControl or control.AriaRole == "textbox":
                return control
            if control.NativeWindowHandle == target.hwnd:
                break
            control = control.GetParentControl()
        # Some web apps use a plain editable <div> (a GroupControl with no textbox role).
        # Accept the control that holds the typing cursor when it covers the remembered point.
        focused = uia.GetFocusedControl()
        if focused and focused.IsKeyboardFocusable and not focused.IsPassword and focused.ControlType not in (
                uia.ControlType.ButtonControl, uia.ControlType.HyperlinkControl, uia.ControlType.ListItemControl,
                uia.ControlType.MenuItemControl, uia.ControlType.TabItemControl, uia.ControlType.WindowControl,
                uia.ControlType.PaneControl, uia.ControlType.DocumentControl):
            rect = focused.BoundingRectangle
            x, y = target.point
            if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom and rect.height() < 400:
                return focused
        return None

    def box_bounds(self, target):
        import uiautomation as uia

        try:
            with uia.UIAutomationInitializerInThread():
                editor = self.editor_at(uia, target)
                if editor:
                    rect = editor.BoundingRectangle
                    if rect.width() > 0 and rect.height() > 0:
                        return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            logging.exception("Could not find the box edge")
        return None

    def last_message(self, target):
        if not target.valid(self) or not self.activate(target.hwnd):
            return ""
        import uiautomation as uia

        with uia.UIAutomationInitializerInThread():
            if target.point and self.window_at(target.point) == target.hwnd:
                editor = self.editor_at(uia, target)
                if editor and not editor.IsPassword:
                    document = editor.GetParentControl()
                    while document:
                        if document.ControlType == uia.ControlType.DocumentControl:
                            return self.message_above(uia, document, editor)
                        if document.NativeWindowHandle == target.hwnd:
                            break
                        document = document.GetParentControl()
            window = uia.ControlFromHandle(target.hwnd)
            if window:
                return self.message_above(uia, window)
        return ""

    @staticmethod
    def message_above(uia, document, editor=None):
        box = editor.BoundingRectangle if editor else None
        page = document.BoundingRectangle
        skip = (uia.ControlType.ButtonControl, uia.ControlType.EditControl,
                uia.ControlType.MenuControl, uia.ControlType.ToolBarControl)
        candidates = []
        pending = [(control, False) for control in reversed(document.GetChildren())]
        while pending:
            control, in_log = pending.pop()
            if (control.IsOffscreen or control.IsPassword or (editor and uia.ControlsAreSame(control, editor))
                    or control.ControlType in skip):
                continue
            in_log = in_log or control.AriaRole == "log"
            children = control.GetChildren()
            if children:
                pending.extend((child, in_log) for child in reversed(children))
                continue
            if control.ControlType != uia.ControlType.TextControl:
                continue
            rect = control.BoundingRectangle
            if box:
                in_bounds = (max(box.left, page.left) < rect.right
                             and rect.left < min(box.right, page.right)
                             and page.top < rect.bottom <= box.top)
            else:
                # With no editor as a reference, use only explicit chat logs.
                in_bounds = in_log and page.top < rect.bottom <= page.bottom
            if in_bounds and rect.height() > 0 and rect.width() > 0:
                name = control.Name.strip()
                if name:
                    candidates.append((in_log, rect.bottom, len(candidates), name))
        return max(candidates)[3] if candidates else ""

    def point_text(self, target):
        if not target.valid(self) or self.window_at(target.point) != target.hwnd:
            return ""
        import uiautomation as uia

        with uia.UIAutomationInitializerInThread():
            element = uia.ControlFromPoint(*target.point)
            for _ in range(4):
                if not element:
                    break
                if element.IsPassword:
                    return ""
                pattern = element.GetPattern(uia.PatternId.TextPattern)
                if pattern:
                    selection = " ".join(item.GetText(6000) for item in pattern.GetSelection()).strip()
                    if selection:
                        return selection
                    nearest = pattern.RangeFromPoint(*target.point)
                    if nearest:
                        nearest.ExpandToEnclosingUnit(uia.TextUnit.Paragraph, waitTime=0)
                        text = nearest.GetText(6000).strip()
                        if text:
                            return text
                if element.ControlType == uia.ControlType.EditControl:
                    pattern = element.GetPattern(uia.PatternId.ValuePattern)
                    if pattern and pattern.Value.strip():
                        return pattern.Value.strip()
                if element.ControlType == uia.ControlType.TextControl and element.Name.strip():
                    return element.Name.strip()
                if element.NativeWindowHandle == target.hwnd:
                    break
                element = element.GetParentControl()
        return ""
