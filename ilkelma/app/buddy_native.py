"""Keep native Buddy windows from activating when clicked."""

import ctypes
from ctypes import wintypes
import logging
import os

import win32con
import win32gui
import win32process

USER = ctypes.WinDLL("user32", use_last_error=True)
COMCTL = ctypes.WinDLL("comctl32", use_last_error=True)
LONG_PTR = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
GetWindowLongPtr = getattr(USER, "GetWindowLongPtrW", USER.GetWindowLongW)
SetWindowLongPtr = getattr(USER, "SetWindowLongPtrW", USER.SetWindowLongW)
GetWindowLongPtr.argtypes = (wintypes.HWND, ctypes.c_int)
GetWindowLongPtr.restype = LONG_PTR
SetWindowLongPtr.argtypes = (wintypes.HWND, ctypes.c_int, LONG_PTR)
SetWindowLongPtr.restype = LONG_PTR
SubclassProc = ctypes.WINFUNCTYPE(
    LONG_PTR, wintypes.HWND, wintypes.UINT, UINT_PTR, LONG_PTR, UINT_PTR, UINT_PTR,
)
COMCTL.SetWindowSubclass.argtypes = (wintypes.HWND, SubclassProc, UINT_PTR, UINT_PTR)
COMCTL.RemoveWindowSubclass.argtypes = (wintypes.HWND, SubclassProc, UINT_PTR)
COMCTL.DefSubclassProc.argtypes = (wintypes.HWND, wintypes.UINT, UINT_PTR, LONG_PTR)
COMCTL.DefSubclassProc.restype = LONG_PTR
GetCurrentThreadId = ctypes.WinDLL("kernel32").GetCurrentThreadId
GetCurrentThreadId.restype = wintypes.DWORD
SetWindowDisplayAffinity = USER.SetWindowDisplayAffinity
SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
SetWindowDisplayAffinity.restype = wintypes.BOOL
WDA_EXCLUDEFROMCAPTURE = 0x11


PAD_TITLES = {"Il-Karta", "Il-Pinna", "Il-Kitba", "Kelma Pad"}


def exclude_app_windows():
    """Exclude every top-level window in this process; return any failed window bounds."""
    pid = os.getpid()
    failed = []

    def exclude(hwnd, _):
        try:
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                # The Kelma Pad is the user's own writing: keep it visible to screenshots and
                # screen sharing. It never sits over a chat box that Il-Kelma reads.
                if win32gui.GetWindowText(hwnd) in PAD_TITLES:
                    SetWindowDisplayAffinity(hwnd, 0)  # WDA_NONE
                    return True
                if not SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                    logging.debug("Could not exclude window %#x from capture", hwnd)
                    if win32gui.IsWindowVisible(hwnd):
                        failed.append(win32gui.GetWindowRect(hwnd))
        except win32gui.error:
            logging.debug("A window disappeared while setting capture exclusion", exc_info=True)
        return True

    win32gui.EnumWindows(exclude, None)
    return tuple(failed)


@SubclassProc
def no_activate(hwnd, message, wparam, lparam, subclass_id, data):
    if message == win32con.WM_MOUSEACTIVATE:
        return win32con.MA_NOACTIVATE
    if message == win32con.WM_PARENTNOTIFY and wparam & 0xFFFF == win32con.WM_CREATE:
        protect_window(lparam)
    if message == win32con.WM_NCDESTROY:
        COMCTL.RemoveWindowSubclass(hwnd, no_activate, subclass_id)
    return COMCTL.DefSubclassProc(hwnd, message, wparam, lparam)


def protect_window(hwnd, _=None):
    try:
        style = GetWindowLongPtr(hwnd, win32con.GWL_EXSTYLE)
        if not style & win32con.WS_EX_NOACTIVATE:
            SetWindowLongPtr(hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_NOACTIVATE)
            if not GetWindowLongPtr(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_NOACTIVATE:
                logging.warning("Could not set Buddy's no-activate style on %#x", hwnd)
        # Windows only allows subclassing on the window's own thread. A child
        # using the default mouse-activate handling asks its protected parent.
        if win32process.GetWindowThreadProcessId(hwnd)[0] == GetCurrentThreadId():
            if not COMCTL.SetWindowSubclass(hwnd, no_activate, 1, 0):
                logging.warning("Could not install Buddy's mouse-activate handler on %#x", hwnd)
    except win32gui.error:
        logging.debug("A Buddy native window disappeared during refresh", exc_info=True)
    return True


def protect_windows(root):
    protect_window(root)
    win32gui.EnumChildWindows(root, protect_window, None)
