"""Kelma Buddy prototype: a floating character that reacts to what Il-Kelma does.

It reads Il-Kelma's log (out/ilkelma.log) and changes mood. It is a separate
test window; it does not change the app. Close it from the tray-less window
with a right click.

Demo control (local only): http://127.0.0.1:3421/mood?key=laugh
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOG = ROOT / "out" / "ilkelma.log"
NAMES = HERE / "names.json"

# log text -> (mood, seconds to hold before going back to rest; 0 = stay)
RULES = [
    ("Recording target", "listen", 0),
    ("Processing audio with duration", "think", 0),
    ("Paste text placed on clipboard", "done", 4),
    ("Read aloud finished", "done", 4),
    ("Read aloud:", "read", 0),
    (" ERROR ", "oops", 5),
    ("Traceback", "oops", 5),
    ("Hotkey 1", "surprise", 3),
]


def names():
    try:
        data = json.loads(NAMES.read_text(encoding="utf-8"))
        return data.get("user", "Clifford"), data.get("buddy", "Kelma")
    except (OSError, ValueError):
        return "Clifford", "Kelma"


class Bridge(QObject):
    mood = Signal(str, int)
    menu = Signal(bool)
    drag = Signal(bool)


KEYS = {"write": 0x78, "read": 0x7A, "help": 0x77}  # F9, F11, F8 (Il-Kelma hotkeys)


def press(vk):
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


class Buddy(QWebEngineView):
    def __init__(self, bridge):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.setContextMenuPolicy(Qt.NoContextMenu)
        area = QApplication.primaryScreen().availableGeometry()
        self.anchor = (area.right() - 16, area.bottom() - 8)   # bottom-right corner of the window
        self.is_open = False
        self.set_open(False)
        self.load(QUrl("http://127.0.0.1:3421/buddy.html"))
        self.back = QTimer(self, singleShot=True)
        self.back.timeout.connect(lambda: self.show_mood("rest", 0))
        self.follow = QTimer(self)
        self.follow.timeout.connect(self.follow_cursor)
        bridge.mood.connect(self.show_mood)
        bridge.menu.connect(self.set_open)
        bridge.drag.connect(self.set_drag)

    def set_open(self, is_open):
        self.is_open = is_open
        w, h = (320, 300) if is_open else (250, 215)
        self.setGeometry(self.anchor[0] - w, self.anchor[1] - h, w, h)
        self.page().runJavaScript(f"document.body.classList.toggle('open', {'true' if is_open else 'false'})")

    def set_drag(self, on):
        from PySide6.QtGui import QCursor
        if on:
            c = QCursor.pos()
            self.grab_offset = (self.anchor[0] - c.x(), self.anchor[1] - c.y())
            self.follow.start(15)
        else:
            self.follow.stop()

    def follow_cursor(self):
        import ctypes
        from PySide6.QtGui import QCursor
        c = QCursor.pos()
        self.anchor = (c.x() + self.grab_offset[0], c.y() + self.grab_offset[1])
        self.move(self.anchor[0] - self.width(), self.anchor[1] - self.height())
        if not ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:   # mouse let go
            self.follow.stop()

    def show_mood(self, key, hold):
        user, buddy = names()
        self.page().runJavaScript(f"setMood({json.dumps(key)}, {json.dumps(user)}, {json.dumps(buddy)})")
        self.back.stop()
        if hold:
            self.back.start(hold * 1000)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            QApplication.quit()


def watch_log(bridge):
    pos = LOG.stat().st_size if LOG.exists() else 0

    def tick():
        nonlocal pos
        try:
            size = LOG.stat().st_size
        except OSError:
            return
        if size < pos:
            pos = 0
        if size == pos:
            return
        with LOG.open("rb") as f:
            f.seek(pos)
            chunk = f.read()
        pos = size
        for line in chunk.decode("utf-8", "ignore").splitlines():
            for text, mood, hold in RULES:
                if text in line:
                    bridge.mood.emit(mood, hold)
                    break

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(150)
    return timer


def serve(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            q = parse_qs(url.query)
            if url.path == "/buddy.html":
                data = (HERE / "buddy.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path == "/menu":
                bridge.menu.emit(q.get("open", ["0"])[0] == "1")
            elif url.path == "/drag":
                bridge.drag.emit(q.get("on", ["0"])[0] == "1")
            elif url.path == "/act":
                vk = KEYS.get(q.get("k", [""])[0])
                if vk:
                    press(vk)
            if url.path in ("/menu", "/act", "/drag"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
            if url.path == "/mood":
                q = parse_qs(url.query)
                bridge.mood.emit(q.get("key", ["rest"])[0], int(q.get("hold", ["0"])[0]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 3421), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


if __name__ == "__main__":
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing")
    app = QApplication(sys.argv)
    bridge = Bridge()
    serve(bridge)
    view = Buddy(bridge)
    view.show()
    _t = watch_log(bridge)
    sys.exit(app.exec())
