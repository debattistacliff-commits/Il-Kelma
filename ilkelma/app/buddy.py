"""Floating Buddy and the feedback state that drives its face."""
import ctypes
from collections import deque
import json
import logging
from pathlib import Path
import re

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QRegion
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from ilkelma.languages import buddy_message, chosen_codes, flag_svg, packs
from .buddy_native import protect_windows
from .widgets import TOOL

FLAGS = TOOL | Qt.WindowType.WindowDoesNotAcceptFocus
SIZE = 104
STATE_MOODS = {"idle": "rest", "listening": "listen", "busy": "think",
               "reading": "read", "check": "rest", "preview": "idea"}


def mood_for(state, mic_available):
    mood = STATE_MOODS[state]
    return "sleep" if mood == "rest" and not mic_available else mood


def reaction(text):
    if re.search(r"\b(?:grazzi|thank\s+you|grazie)\b", text, re.IGNORECASE):
        return "thanks"
    if re.search(r"\b(?:ħa\s*ħa|haha|lol)\b", text, re.IGNORECASE):
        return "laugh"
    return None


def next_language(language):
    languages = chosen_codes()
    return languages[(languages.index(language) + 1) % len(languages)] if language in languages else languages[0]


def feedback(language, key, **values):
    message = buddy_message(language, key)[0]
    mood, speak = packs()["en"]["buddy_feedback"][key]
    return message.format(**values), mood, speak


class Buddy(QWebEngineView):
    action = Signal(str)
    moved = Signal(QPoint)

    def __init__(self, name, language):
        super().__init__()
        self.setWindowFlags(FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.anchor = QApplication.primaryScreen().availableGeometry().bottomRight() - QPoint(122, 128)
        self.name, self.language = name, language
        self.comments_active = self.comments_reading = False
        self.listen_active = False
        self.visible_regions = []
        self.clipped = self.open = self.context = self.loaded = False
        self.base_mood = "rest"
        self.temporary_mood = self.message = ""
        self.recent_messages = deque(maxlen=8)
        self.message_timer = QTimer(self, singleShot=True)
        self.message_timer.timeout.connect(self.dismiss)
        self.mood_timer = QTimer(self, singleShot=True)
        self.mood_timer.timeout.connect(self.end_mood)
        self.follow = QTimer(self, interval=15)
        self.follow.timeout.connect(self.follow_cursor)
        self.channel = QWebChannel(self.page())
        self.channel.registerObject("buddy", self)
        self.page().setWebChannel(self.channel)
        self.loadFinished.connect(self.report_load)
        # WebEngine can create or replace native children after show or load.
        self.native_timer = QTimer(self, interval=100)
        self.native_timer.timeout.connect(self.protect_native_windows)
        self.refresh()
        self.load_page()

    def load_page(self):
        """Load the page with a flag for each chosen language."""
        path = Path(__file__).with_name("buddy.html")
        flags = "".join(flag_svg(code, hidden=index > 0) for index, code in enumerate(chosen_codes()))
        self.loaded = False
        self.setHtml(path.read_text(encoding="utf-8").replace("<!--FLAGS-->", flags), QUrl.fromLocalFile(str(path)))

    def protect_native_windows(self):
        protect_windows(int(self.winId()))

    def showEvent(self, event):
        self.protect_native_windows()
        super().showEvent(event)
        self.protect_native_windows()
        self.native_timer.start()

    def hideEvent(self, event):
        self.native_timer.stop()
        super().hideEvent(event)

    def report_load(self, ok):
        if self.isVisible():
            self.protect_native_windows()
        if not ok:
            logging.error("Could not load the Kelma Buddy view")

    def set_position(self, point):
        if self.anchor != point:
            self.anchor = point
            self.refresh()

    def screen_area(self):
        screen = QApplication.screenAt(self.anchor + QPoint(SIZE // 2, SIZE // 2)) or self.screen()
        return screen.availableGeometry()

    def visible_position(self, area):
        return QPoint(max(area.left(), min(self.anchor.x(), area.right() - SIZE)),
                      max(area.top(), min(self.anchor.y(), area.bottom() - SIZE - 18)))

    def set_state(self, mood):
        if self.base_mood != mood:
            self.base_mood = mood
            self.mood_timer.stop()
            self.temporary_mood = ""
            self.refresh()

    def show_message(self, text, mood, hold=None):
        duration = max(6000, 1500 + len(text.split()) * 380)
        self.message = text
        self.recent_messages.append(text)
        self.message_timer.start(duration)
        self.set_reaction(mood, hold or duration)

    def set_reaction(self, mood, duration):
        self.temporary_mood = mood
        self.mood_timer.start(duration)
        self.refresh()

    def dismiss(self):
        self.message_timer.stop()
        self.message = ""
        self.refresh()

    def end_mood(self):
        self.temporary_mood = ""
        self.refresh()

    def close_menu(self):
        self.open = self.context = False
        self.refresh()

    def refresh(self):
        area = self.screen_area()
        point = self.visible_position(area)
        x, y = point.x(), point.y()
        side = -1 if x - area.left() >= area.right() - x - SIZE else 1
        vertical = -1 if y - area.top() >= area.bottom() - y - SIZE else 1
        around = self.open or self.context
        has_bubble = bool(self.message)
        horizontal_room = 245 if around else 84 if has_bubble else 12
        upper = ((292 if self.open else 142) if has_bubble and vertical < 0 else
                 168 if self.open and vertical < 0 else 12)
        lower = (390 if self.open else 265) if has_bubble and vertical > 0 else 242 if around else 132
        if self.open:
            # Keep room for a Buddy-centered arc, including rotated arcs on small screens.
            upper = max(upper, 150)
            lower = max(lower, SIZE + 150)
        if self.context:
            upper, lower = max(upper, 210), max(lower, 320)
        bounds = QRect(x - horizontal_room, y - upper, SIZE + horizontal_room * 2, upper + lower)
        bounds = bounds.intersected(area)
        if self.geometry() != bounds:
            self.setGeometry(bounds)
        state = dict(x=x - bounds.x(), y=y - bounds.y(), side=side, vertical=vertical,
                     open=self.open, context=self.context, clipped=self.clipped, name=self.name,
                     language=(self.language or "mt").upper(), message=self.message,
                     languageName=packs().get(self.language or "mt", {}).get("name", ""),
                     comments=self.comments_active, commentsReading=self.comments_reading,
                     listenActive=self.listen_active,
                     mood=self.temporary_mood or ("read" if self.comments_reading else self.base_mood))
        if self.loaded:
            self.page().runJavaScript(f"render({json.dumps(state)})")
        else:
            self.setMask(QRegion(QRect(state["x"], state["y"], SIZE, SIZE + 18)))

    @Slot()
    def ready(self):
        self.loaded = True
        self.refresh()

    @Slot("QVariantList")
    def regions(self, rectangles):
        region = QRegion()
        self.visible_regions = [QRect(*rect) for rect in rectangles]
        for rect in self.visible_regions:
            region |= QRegion(rect)
        self.setMask(region)

    @Slot(str)
    def command(self, action):
        if action == "menu":
            self.open = not self.open
            self.context = False
            self.refresh()
        elif action == "context":
            self.context = not self.context
            self.open = False
            self.refresh()
        elif action == "dismiss":
            self.dismiss()
        elif action == "drag" and not self.clipped:
            self.grab_offset = self.anchor - QCursor.pos()
            self.close_menu()
            self.follow.start()
        elif action == "drop":
            self.finish_drag()
        elif action in {"write", "read", "help", "last", "lang", "clip", "settings", "hide", "quit",
                        "point", "comments", "stop-comments", "draw-comments", "listen-learn", "pad"}:
            if action not in {"lang", "clip"} and (action != "comments" or self.context):
                self.close_menu()
            self.action.emit(action)

    def follow_cursor(self):
        if not ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000:
            self.finish_drag()
            return
        self.set_position(QCursor.pos() + self.grab_offset)

    def finish_drag(self):
        if self.follow.isActive():
            self.follow.stop()
            self.set_position(self.visible_position(self.screen_area()))
            self.moved.emit(self.anchor)
