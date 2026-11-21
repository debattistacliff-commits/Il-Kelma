import ctypes
from ctypes import wintypes
import logging

from PySide6.QtCore import QAbstractNativeEventFilter

USER = ctypes.WinDLL("user32", use_last_error=True)
USER.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
USER.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
WM_HOTKEY = 0x0312
NO_REPEAT = 0x4000
KEYS = {f"F{i}": (0, 111 + i) for i in range(1, 12)}
KEYS.update({"Esc": (0, 27), "Ctrl+Alt+R": (0x0003, ord("R")), "Ctrl+Alt+H": (0x0003, ord("H"))})
READ_FALLBACKS = ("F10", "F8", "Ctrl+Alt+R")
HELP_FALLBACKS = ("Ctrl+Alt+H", "F7", "F6")
KEYS["Ctrl+Alt+P"] = (0x0003, ord("P"))
POINT_FALLBACKS = ("Ctrl+Alt+P", "F6")
KEYS["Ctrl+Alt+C"] = (0x0003, ord("C"))
COMMENTS_FALLBACKS = ("Ctrl+Alt+C", "F5")
KEYS["Ctrl+Alt+K"] = (0x0003, ord("K"))


def bind_startup(hotkeys, slot, preferred, fallbacks):
    for key in dict.fromkeys((preferred, *fallbacks)):
        if hotkeys.bind(slot, key):
            return key
    return ""


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, window, callbacks):
        super().__init__()
        self.hwnd = int(window.winId())
        self.callbacks = callbacks
        self.bound = {}

    def bind(self, slot, key):
        if self.bound.get(slot) == key:
            return True
        if key not in KEYS or key in self.bound.values():
            logging.warning("Hotkey %s (%s) could not be registered: invalid or already assigned", slot, key)
            return False
        previous = self.bound.get(slot)
        self.unbind(slot)
        modifiers, vk = KEYS[key]
        if USER.RegisterHotKey(self.hwnd, slot, NO_REPEAT | modifiers, vk):
            self.bound[slot] = key
            logging.info("Hotkey %s (%s) registered", slot, key)
            return True
        logging.warning("Hotkey %s (%s) could not be registered (Windows error %s)",
                        slot, key, ctypes.get_last_error())
        if previous:
            self.bind(slot, previous)
        return False

    def unbind(self, slot):
        if slot in self.bound:
            USER.UnregisterHotKey(self.hwnd, slot)
            del self.bound[slot]

    def close(self):
        for slot in tuple(self.bound):
            self.unbind(slot)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            event = wintypes.MSG.from_address(int(message))
            if event.hWnd == self.hwnd and event.message == WM_HOTKEY:
                callback = self.callbacks.get(event.wParam)
                if callback:
                    callback()
                    return True, 0
        return False, 0
