"""Buddy's read-only screen controls."""
from collections import deque
import logging
import re
from threading import Event
import sqlite3
from time import monotonic
from unicodedata import category

import win32api
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ilkelma.engine.comment_reading import (
    comment_language, comment_speech, readable_comment,
)
from ilkelma.engine.learned_words import WordStore, tokens
from ilkelma.engine.screen_ocr import (
    NewComments, capture, clean_comment, comment_key, point_box, pointed_comment, pointed_text,
    prepare, read_chat,
)
from ilkelma.engine.tts import announce, play
from ilkelma.engine.windows import screen_origins
from .buddy_native import exclude_app_windows
from .learning import corrected
from .storage import load, save


COMMENT_STATUS = {
    "mt": ("Qed naqra l-kummenti", "Waqaft"),
    "en": ("Reading comments", "Stopped"),
    "it": ("Sto leggendo i commenti", "Mi sono fermato"),
}


def comments_key(process, title):
    title = " ".join(title.casefold().split())
    title = re.split(r" (?:-|\|) ", title)[-1]
    title = re.sub(r"\b(?:live|on)\b", "", title)
    title = " ".join(title.split())
    while title and category(title[-1]).startswith("P"):
        title = title[:-1].rstrip()
    return f"{process.casefold()}|{title}" if title else None


def inside_window(box, bounds):
    left, top, right, bottom = bounds
    x1, y1, x2, y2 = box
    return left <= x1 < x2 <= right and top <= y1 < y2 <= bottom



class CaptureExclusion(QObject):
    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.Show and isinstance(watched, QWidget)
                and watched.isWindow()):
            exclude_app_windows()
        return False


def physical_box(rect, screen, origins):
    geometry = screen.geometry()
    left, top = origins.get(screen.name(), (geometry.x(), geometry.y()))
    scale = screen.devicePixelRatio()
    x = (rect.x() - geometry.x()) * scale
    y = (rect.y() - geometry.y()) * scale
    return (left + round(x), top + round(y),
            left + round(x + rect.width() * scale), top + round(y + rect.height() * scale))


class ChatOutline(QWidget):
    def __init__(self, screen, finish):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())
        self.display = screen
        self.finish = finish
        self.start = None
        self.outline = QRect()

    def paintEvent(self, event):
        painter = QPainter(self)
        # A translucent fill also catches clicks across the entire overlay.
        painter.fillRect(self.rect(), QColor(19, 34, 58, 48))
        if self.outline.isValid():
            painter.setPen(QPen(QColor("#69bcff"), 2))
            painter.drawRect(self.outline)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.outline = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
            self.start = None
            if rect.width() >= 20 and rect.height() >= 20:
                self.finish(physical_box(rect.translated(self.pos()), self.display, screen_origins()))
            else:
                self.outline = QRect()
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finish(None)


class ScreenReader(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.buddy = window.buddy
        self.outlines = []
        self.selection_target = None
        self.region = self.tracker = None
        self.paused = False
        self.scan_token = None
        saved = load("comment_boxes", {})
        self.saved_boxes = saved if isinstance(saved, dict) else {}
        self.queue = deque(maxlen=5)
        self.point_timer = QTimer(self, interval=50)
        self.point_timer.timeout.connect(self.wait_for_point)
        self.chat_timer = QTimer(self, interval=2000)
        self.chat_timer.timeout.connect(self.poll)
        self.capture_pending = False
        self.point_request = None
        self.point_token = None
        self.point_result = None
        self.speech = None
        self.words = WordStore()
        self.learned_comments = set()
        self.word_timer = QTimer(self, interval=3600000)
        self.word_timer.timeout.connect(self.clean_words)
        self.word_timer.start()
        self.clean_words()

    def clean_words(self):
        try:
            self.words.cleanup()
        except (OSError, sqlite3.Error):
            logging.warning("Could not remove expired learned words")

    def forget_words(self):
        try:
            self.words.forget()
        except (OSError, sqlite3.Error):
            self.buddy.show_message("Could not forget learned words.", "oops")
        else:
            self.buddy.show_message("Learned words cleared.", "done")

    def learn_comment(self, pair, language=None):
        if not self.window.settings["learn_read_words"]:
            return
        fingerprint = comment_key(pair)
        if fingerprint in self.learned_comments:
            return
        name, message = pair
        try:
            self.words.observe(message, excluded={word for _, word, *_ in tokens(name)},
                               fallback=self.language, language=language)
        except (OSError, sqlite3.Error, ValueError):
            logging.warning("Could not learn words from the comment")
        else:
            self.learned_comments.add(fingerprint)

    @property
    def available(self):
        return self.window.state in ("idle", "check") and not self.window.pending_phrase

    @property
    def language(self):
        return self.window.settings["language"] or "mt"

    def point(self, immediate=False):
        if not self.available or self.outlines:
            return
        if not self.buddy.isVisible():
            self.buddy.show()
        self.buddy.close_menu()
        self.point_timer.stop()
        self.point_token = object()
        self.point_request = self.point_result = None
        if self.speech:
            self.speech[0].set()
        if immediate:
            self.read_point()
            return
        self.buddy.show_message("Ipponta u stenna", "read")
        self.window.stop_bubble_audio.set()
        if not self.speech and self.window.settings["buddy_speaks"]:
            self.speak("Ipponta u stenna", "mt", prompt=True)
        now = monotonic()
        self.start_point = self.last_point = QCursor.pos()
        self.rest_since, self.point_deadline = now, now + 20
        self.point_timer.start()

    def wait_for_point(self):
        now = monotonic()
        point = QCursor.pos()
        if not self.available or now >= self.point_deadline:
            self.point_timer.stop()
            self.point_token = None
            self.next_speech()
        elif (point - self.last_point).manhattanLength() > 3:
            self.last_point, self.rest_since = point, now
        elif (point - self.start_point).manhattanLength() > 8 and now - self.rest_since >= .6:
            self.point_timer.stop()
            self.read_point()

    def read_point(self):
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            self.point_token = None
            return
        point = win32api.GetCursorPos()
        box = point_box(point, physical_box(screen.geometry(), screen, screen_origins()))
        self.point_request = box, point, self.point_token
        self.poll()

    def toggle_comments(self):
        if self.region or self.outlines:
            self.stop_comments()
            return
        if not self.available:
            return
        target = self.window.foreground_tracker.current()
        if target:
            window = self.window.desktop.comments_window(target.hwnd)
            if window:
                process, title, bounds = window
                offsets = self.saved_boxes.get(comments_key(process, title))
                if (isinstance(offsets, list) and len(offsets) == 4
                        and all(type(value) is int for value in offsets)):
                    box = tuple(origin + offset for origin, offset in zip(bounds[:2] * 2, offsets))
                    if inside_window(box, bounds):
                        self.start_comments(box)
                        return
        self.draw_comments()

    def draw_comments(self):
        if not self.available:
            return
        screens = QApplication.screens()
        if not screens:
            return
        self.stop_comments(message=None)
        self.pause()
        self.buddy.show_message("Drag a box around the comments. Press Esc to cancel.", "read")
        self.selection_target = self.window.foreground_tracker.current()
        self.outlines = [ChatOutline(screen, self.selected) for screen in screens]
        active = next((outline for outline in self.outlines if outline.geometry().contains(QCursor.pos())),
                      self.outlines[0])
        for outline in self.outlines:
            outline.show()
        self.window.hotkeys.bind(6, "Esc")
        active.raise_()
        active.activateWindow()

    def selected(self, box):
        self.window.hotkeys.unbind(6)
        for outline in self.outlines:
            outline.hide()
            outline.deleteLater()
        self.outlines.clear()
        target, self.selection_target = self.selection_target, None
        if box is None:
            self.buddy.dismiss()
            return
        x1, y1, x2, y2 = box
        desktop = self.window.desktop
        hwnd = desktop.window_at(((x1 + x2) // 2, (y1 + y2) // 2))
        window = desktop.comments_window(hwnd)
        if not window and target and target.valid(desktop):
            window = desktop.comments_window(target.hwnd)
        if window:
            process, title, bounds = window
            key = comments_key(process, title)
            if key and inside_window(box, bounds):
                self.saved_boxes[key] = [coordinate - origin
                                         for coordinate, origin in zip(box, bounds[:2] * 2)]
                try:
                    save("comment_boxes", self.saved_boxes)
                except OSError:
                    logging.exception("Could not save the comments box")
        self.start_comments(box)

    def region_points(self):
        x1, y1, x2, y2 = self.region
        dx, dy = max(1, (x2 - x1) // 8), max(1, (y2 - y1) // 8)
        return [((x1 + x2) // 2, (y1 + y2) // 2), (x1 + dx, y1 + dy), (x2 - dx, y1 + dy),
                (x1 + dx, y2 - dy), (x2 - dx, y2 - dy)]

    def region_visible(self):
        """True when the chat window itself (or Il-Kelma's own Buddy) is what shows inside the box."""
        owner = getattr(self, "region_owner", None)
        if not owner or not self.region:
            return True
        import os
        import win32process
        desktop = self.window.desktop
        for point in self.region_points():
            hwnd = desktop.window_at(point)
            if hwnd == owner:
                continue
            try:
                if hwnd and win32process.GetWindowThreadProcessId(hwnd)[1] == os.getpid():
                    continue
            except Exception:
                pass
            return False
        return True

    def start_comments(self, box):
        self.pause()
        self.region, self.tracker = box, NewComments(box[3] - box[1])
        try:
            x1, y1, x2, y2 = box
            self.region_owner = self.window.desktop.window_at(((x1 + x2) // 2, (y1 + y2) // 2))
        except Exception:
            self.region_owner = None
        self.paused = False
        self.buddy.comments_active = self.buddy.comments_reading = True
        self.buddy.show_message(self.comment_status(True), "read")
        self.chat_timer.start()
        QTimer.singleShot(100, self.poll)

    def comment_status(self, reading):
        return COMMENT_STATUS.get(self.language, COMMENT_STATUS["mt"])[0 if reading else 1]

    def stop_comments(self, message=True):
        self.queue.clear()
        if self.speech and self.speech[1] == "comment":
            self.speech[0].set()
        self.region = self.tracker = None
        self.paused = False
        self.scan_token = None
        self.chat_timer.stop()
        if self.outlines:
            self.selected(None)
        self.buddy.comments_active = self.buddy.comments_reading = False
        if message is True:
            self.buddy.show_message(self.comment_status(False), "rest")
        elif message:
            self.buddy.show_message(message, "oops")
        else:
            self.buddy.refresh()

    def cancel_selection(self):
        if self.outlines:
            self.selected(None)

    def pause(self):
        self.cancel_selection()
        self.point_timer.stop()
        self.point_token = self.point_request = self.point_result = None
        if self.speech:
            self.speech[0].set()
        if self.region and not self.paused:
            self.paused = True
            self.scan_token = None
            self.queue.clear()
            self.chat_timer.stop()
            self.tracker.previous.clear()
            self.buddy.comments_reading = False
            self.buddy.refresh()

    def resume(self):
        if self.paused and self.available:
            self.paused = False
            self.buddy.comments_reading = True
            self.buddy.refresh()
            self.chat_timer.start()
            self.poll()

    def poll(self):
        if self.capture_pending or not self.available or self.outlines:
            return
        request, tracker = self.point_request, None if self.paused else self.tracker
        if request is None and tracker is None:
            return
        if request is None and not self.region_visible():
            return  # something else (e.g. a Windows panel) covers the chat: wait, read nothing
        self.point_request = None
        box = request[0] if request else self.region
        language = self.language
        region = self.region
        bubbles = tuple(self.buddy.recent_messages)
        self.capture_pending = True
        token = self.scan_token = object()

        def scan():
            try:
                lines = capture(box, language, exclude_app_windows(), sample_ink=request is None)
                if request:
                    x, y = request[1]
                    point = x - box[0], y - box[1]
                    in_chat = region and region[0] <= x < region[2] and region[1] <= y < region[3]
                    chat_lines = lines
                    if in_chat:
                        chat_lines = [line for line in lines
                                      if region[0] <= line.box[0] + box[0]
                                      and region[1] <= line.box[1] + box[1]
                                      and line.box[2] + box[0] <= region[2]
                                      and line.box[3] + box[1] <= region[3]]
                    pair = pointed_comment(chat_lines, point, in_chat=in_chat)
                    return (pair[1] if pair else pointed_text(lines, point), pair)
                return lines
            except Exception:
                logging.exception("Could not read the screen")
                return None

        def scanned(result):
            self.capture_pending = False
            if request and request[2] is self.point_token:
                self.point_token = None
                if result and result[0]:
                    self.point_result = result
                else:
                    self.buddy.show_message("No text found here." if result is not None else
                                            "Could not read the screen.", "oops")
            elif not request and tracker is self.tracker and token is self.scan_token:
                if result is None:
                    self.stop_comments("Could not read the comments.")
                else:
                    fresh = tracker.update(read_chat(result, (*bubbles, *self.buddy.recent_messages)))
                    self.queue.extend(pair for pair in fresh
                                      if readable_comment(pair[1], self.window.settings))
            self.next_speech()
            if self.point_request:
                self.poll()

        self.window.run(scan, scanned)
        self.next_speech()

    def next_speech(self):
        if self.speech or not self.available or self.outlines or self.point_timer.isActive():
            return
        if self.point_result:
            (text, pair), self.point_result = self.point_result, None
            if pair:
                if self.read_comment(pair):
                    return
            else:
                text, language = prepare(text, self.language, corrected)
                self.speak(text, language)
                return
        while self.tracker is not None and self.queue and self.point_token is None:
            if self.read_comment(self.queue.popleft(), automatic=True):
                return

    def read_comment(self, pair, *, automatic=False):
        name, raw = pair
        settings = self.window.settings
        if not readable_comment(raw, settings):
            return False
        text = clean_comment(raw)
        language = comment_language(text)
        self.learn_comment((name, text), language)
        text, voice, rate = comment_speech(name, text, language, settings, corrected)
        self.speak(text, voice, comment=automatic, rate=rate)
        return True

    def speak(self, text, language, *, prompt=False, comment=False, rate="+0%"):
        if not prompt:
            self.buddy.show_message(text, "read")
        self.window.stop_bubble_audio.set()
        stop = Event()
        self.speech = stop, "comment" if comment else "point"
        online = self.window.settings["voices"]

        def say():
            try:
                if not stop.is_set():
                    if online:
                        play(text, language, stop, rate=rate)
                    elif prompt:
                        announce(text)
                return True
            except Exception:
                logging.exception("Could not speak screen text")
                return False

        def spoken(ok):
            self.speech = None
            if not stop.is_set() and not prompt and self.buddy.message == text:
                self.buddy.set_reaction("done" if ok else "sad", 2500)
            self.next_speech()

        if online or prompt:
            self.window.run(say, spoken)
        else:
            QTimer.singleShot(2000, lambda: spoken(True))
