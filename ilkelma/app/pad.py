"""Clifford's writing pad."""
from difflib import SequenceMatcher
import logging
from math import sin
import unicodedata
from pathlib import Path
import re
from threading import Event

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPalette
from PySide6.QtGui import QPainterPath, QPixmap, QRadialGradient, QShortcut
from PySide6.QtGui import QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidgetAction, QWidget,
)

from maltese_text import WORD
from ilkelma.engine.tts import play_words
from .learning import confirm_phrase
from .pad_text import Pages, fix_letters, qt_position, suggestions
from .storage import DATA
from .pad_widgets import CHIP_GRADIENT, DockAction, SizeSlider, Swatch, ToggleSwitch, gradient, shadow

# Background, paper, ink, title colours, blobs, swatch colours and paper shadow.
THEMES = {
    "Cream": ("#F6EFE3", "rgba(255,252,245,235)", "#2B2118", ("#973803", "#A42358"),
              ("#FFD8A8", "#FFC2D1", "#FFE9A6"), ("#FFF6E6", "#F2DFC0"), "#B4783C"),
    "Sky": ("#E9F1FF", "rgba(250,252,255,235)", "#14213D", ("#214FA7", "#6038AE"),
            ("#A8C8FF", "#C7B8FF", "#9EE6FF"), ("#E3EEFF", "#B9D3FF"), "#3C64C8"),
    "Mint": ("#E6F7EF", "rgba(250,255,252,235)", "#12382A", ("#096449", "#175E93"),
             ("#9FEBC6", "#B5F0E6", "#D9F7A8"), ("#E2F8EC", "#A8E6C8"), "#1E8C64"),
    "Peach": ("#FFEDE4", "rgba(255,250,246,235)", "#3A2418", ("#A33214", "#AA225B"),
              ("#FFB59A", "#FF9EC0", "#FFD98F"), ("#FFE9DD", "#FFC2A8"), "#DC6E46"),
    "Night": ("#0E1424", "rgba(30,38,60,224)", "#E8EEF8", ("#8AB4FF", "#C49BFF"),
              ("#3B3F9A", "#1E5A8C", "#5B2D80"), ("#2A3555", "#0E1424"), "#000000"),
    "Bold": ("#000000", "#000000", "#FFE600", ("#FFE600", "#FFE600"),
             ("#000000", "#000000", "#000000"), ("#000000", "#000000"), "#000000"),
}
HEADER_LIGHT = "#FAF8F5"
HEADER_DARK = "#222B43"
PANEL_WIDTH = 444
PANEL_GAP = 24
SAVED_TEXT = "●  Saved · Issejvjat"
FONTS = ("Lexend", "Atkinson Hyperlegible", "Andika", "Comic Neue")
SPACES = {"Normal": (160, 0, 0), "Wide": (190, 1, 4), "Extra": (220, 2.5, 10)}
MARKS = {"Yellow": "#FFE066", "Green": "#A6F0C6", "Pink": "#FFC2DA", "Blue": "#BFE0FF"}
NAMES = ("Il-Karta", "Il-Pinna", "Il-Kitba", "Kelma Pad")
FLAGS = ("mt", "en", "it")
ROOT = Path(__file__).resolve().parents[2]
ICONS = {
    "speak": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "read": '<path d="M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 0 1 0 7M18.5 6a9 9 0 0 1 0 12"/>',
    "fix": '<path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8zM19 14l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8z"/>',
    "help": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.6-.8L3 21l1.9-5.4A8.5 8.5 0 1 1 21 11.5zM10 9a2 2 0 1 1 3 1.8c-.7.4-1 1-1 1.7M12 16h.1"/>',
    "paste": '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "ruler": '<path d="M2 15 15 2l7 7L9 22zM5 12l3 3m0-6 2 2m1-5 3 3m0-6 2 2"/>',
    "buddy": '<path d="M21 11a8.8 8.8 0 0 1-9 9 9 9 0 0 1-4-.9L3 21l1.8-5.5A9 9 0 1 1 21 11zM9 11h.1m5.9 0h.1"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "new": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M12 12v6M9 15h6"/>',
    "settings": '<path d="M12 22a10 10 0 1 1 10-10c0 2.5-2 3-4 3h-2a2 2 0 0 0-1 3.7A1.6 1.6 0 0 1 12 22zM8 8h.1M6 13h.1M13 6h.1M17 10h.1"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
}


def svg_pixmap(svg):
    pixmap = QPixmap()
    pixmap.loadFromData(svg.encode("utf-8"), "SVG")
    return pixmap


def action_icon(name, colour="white"):
    return QIcon(svg_pixmap(f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="52" height="52" viewBox="0 0 24 24"
             fill="none" stroke="{colour}" stroke-width="2.3" stroke-linecap="round"
             stroke-linejoin="round">{ICONS[name]}</svg>
    """))


class GradientTitle(QLabel):
    colours = THEMES["Cream"][3]

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            shape = QPainterPath()
            metrics = self.fontMetrics()
            shape.addText(1, (self.height() + metrics.ascent() - metrics.descent()) / 2,
                          self.font(), self.text())
            bounds = shape.boundingRect()
            fill = QColor(self.colours[0])
            if bounds.width() > 0 and self.colours[0] != self.colours[1]:
                fill = gradient(bounds, *self.colours)
            painter.fillPath(shape, fill)


class SpeechBubble(QLabel):
    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -8.5)
            base = rect.bottom()
            shape = QPainterPath()
            shape.addRoundedRect(rect, 20, 20)
            tail = QPainterPath(QPointF(22, base - 3))
            tail.lineTo(21, base + 7)
            tail.lineTo(36, base - 3)
            tail.closeSubpath()
            shape = shape.united(tail)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.setBrush(QColor(255, 255, 255, 211))
            painter.drawPath(shape)
            painter.setPen(QColor("#14213D"))
            painter.drawText(rect.adjusted(10, 7, -10, -7),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.text())


class Paper(QTextEdit):
    tapped = Signal(QPoint)
    typed_boundary = Signal(int)
    sentence_finished = Signal()

    def __init__(self):
        super().__init__()
        self.reading_mark = None
        self.ruler = True
        self.autofix = False
        self.follow_read = False
        self.press = None
        self.dragged = False
        self.setCursorWidth(0)
        self.cursorPositionChanged.connect(self.viewport().update)
        self.selectionChanged.connect(self.viewport().update)

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self.viewport()) as painter:
            if self.reading_mark:
                cursor, colour = self.reading_mark
                first = QTextCursor(cursor)
                first.setPosition(cursor.selectionStart())
                last = QTextCursor(cursor)
                last.setPosition(cursor.selectionEnd())
                start, end = self.cursorRect(first), self.cursorRect(last)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if self.ruler:
                    band = QRectF(0, start.top() - 5, self.viewport().width(), start.height() + 10)
                    fade = gradient(band, colour, colour)
                    fade.setColorAt(0, QColor(Qt.GlobalColor.transparent))
                    tint = QColor(colour)
                    tint.setAlpha(67)
                    fade.setColorAt(.5, tint)
                    fade.setColorAt(1, QColor(Qt.GlobalColor.transparent))
                    painter.fillRect(band, fade)
                if start.y() == end.y():
                    glow = QRectF(start.left(), start.top(), end.left() - start.left(), start.height())
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    for spread, alpha in ((4, 42), (2, 90)):
                        tint = QColor(colour)
                        tint.setAlpha(alpha)
                        painter.setPen(tint)
                        painter.drawRoundedRect(glow.adjusted(-spread, -spread, spread, spread), 7, 7)
            if self.hasFocus() and not self.textCursor().hasSelection():
                caret = self.cursorRect()
                caret.setWidth(3)
                painter.fillRect(caret, self.palette().color(QPalette.ColorRole.Text))

    def keyPressEvent(self, event):
        value = event.text()
        boundary = (len(value) == 1 and (value.isspace() or unicodedata.category(value).startswith("P"))
                    and value not in {"'", "’", "-", "‑"}
                    and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
                    and event.key() not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not self.isReadOnly())
        if not boundary or not (self.autofix or self.follow_read):
            super().keyPressEvent(event)
            return
        if not self.autofix:
            super().keyPressEvent(event)
            if self.follow_read and value in ".!?":
                self.sentence_finished.emit()
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        before = cursor.selectionStart()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self.setTextCursor(cursor)
        self.typed_boundary.emit(before)
        super().keyPressEvent(event)
        cursor.endEditBlock()
        if self.follow_read and value in ".!?":
            self.sentence_finished.emit()

    def mousePressEvent(self, event):
        self.press = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        self.dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press is not None:
            distance = (event.position().toPoint() - self.press).manhattanLength()
            self.dragged |= distance >= QApplication.startDragDistance()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        point = event.position().toPoint()
        if (self.press is not None and not self.dragged and not self.textCursor().hasSelection()
                and (point - self.press).manhattanLength() < QApplication.startDragDistance()):
            self.tapped.emit(point)
        self.press = None


class Pad(QWidget):
    boundary = Signal(object, int, int)

    def __init__(self, host, folder=DATA / "pad"):
        super().__init__(host, Qt.WindowType.Window)
        self.host = host
        self.pages = Pages(folder)
        self.options = {
            "theme": "Cream", "font": "Lexend", "size": 30, "spacing": "Wide",
            "mark": "Yellow", "name": "Il-Karta", "panel": False, "ruler": True, "autofix": False,
            "followRead": False, "bigBuddy": False,
        } | host.settings.get("pad", {})
        self.reading = None
        self.followed_sentence = None
        self.read_span = None
        self.flash = []
        self.picked = None
        buddy_html = Path(__file__).with_name("buddy.html").read_text(encoding="utf-8")
        self.art = dict(re.findall(r'id="m-([\w-]+)">(\s*<svg.*?</svg>)', buddy_html, re.S))
        self.flags = re.findall(r'<svg class="flag"[^>]*>.*?</svg>', buddy_html, re.S)
        self.fonts = set()
        for path in (ROOT / "assets" / "fonts").glob("*.ttf"):
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                self.fonts.update(QFontDatabase.applicationFontFamilies(font_id))
        self.setFont(QFont("Plus Jakarta Sans" if "Plus Jakarta Sans" in self.fonts else "Segoe UI", 10))
        self.drift_step = 0
        self.drift = QTimer(self, interval=100)
        self.drift.timeout.connect(self.advance_background)
        self.build()
        try:
            self.editor.setPlainText(self.pages.load())
            self.saved.setText(SAVED_TEXT)
        except (OSError, UnicodeError):
            self.mood("oops", "Could not open the page. / Ma stajtx niftaħ il-paġna.")
        self.apply_style()
        self.setMinimumHeight(560)
        self.layout().activate()
        self.update_count()
        self.editor.document().clearUndoRedoStacks()
        self.editor.textChanged.connect(self.edited)
        self.boundary.connect(self.highlight_word)
        self.autosave = QTimer(self, interval=3500)
        self.autosave.timeout.connect(self.save_page)
        self.flash_timer = QTimer(self, singleShot=True, interval=2000)
        self.flash_timer.timeout.connect(self.clear_flash)
        self.mood_timer = QTimer(self, singleShot=True, interval=2600)
        self.mood_timer.timeout.connect(lambda: self.mood("rest", "Ikteb jew tkellem."))
        geometry = self.options.get("geometry")
        if not (geometry and self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))):
            area = host.screen().availableGeometry()
            self.resize(round(area.width() * .8), round(area.height() * .8))
            self.move(area.topLeft() + QPoint((area.width() - self.width()) // 2,
                                             (area.height() - self.height()) // 2))

    @property
    def language(self):
        return self.language_box.currentData()

    def button(self, label, run):
        button = QPushButton(label)
        button.setToolTip(label.replace("\n", " / "))
        button.clicked.connect(lambda: run())
        return button

    def build(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(30, 24, 30, 24)
        main.setSpacing(18)
        bar = QFrame()
        bar.setObjectName("padHeader")
        shadow(bar)
        header = QHBoxLayout(bar)
        header.setContentsMargins(22, 10, 14, 10)
        self.title = GradientTitle()
        self.title.setObjectName("padTitle")
        header.addWidget(self.title)
        self.language_box = QComboBox()
        for flag, code, label in zip(self.flags, FLAGS, ("Malti", "English", "Italiano")):
            svg = re.sub(r' style="[^"]*"', "", flag)
            pixmap = svg_pixmap(svg).scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
            circle = QPixmap(24, 24)
            circle.fill(Qt.GlobalColor.transparent)
            with QPainter(circle) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                mask = QPainterPath()
                mask.addEllipse(QRectF(0, 0, 24, 24))
                painter.setClipPath(mask)
                painter.drawPixmap(0, 0, pixmap)
            self.language_box.addItem(QIcon(circle), label, code)
        self.language_box.setIconSize(QSize(24, 24))
        self.language_box.setToolTip("Writing language / Lingwa tal-kitba")
        language = self.host.settings.get("language")
        self.language_box.setCurrentIndex(FLAGS.index(language) if language in FLAGS else 0)
        header.addWidget(self.language_box)
        header.addStretch()
        self.saved = QLabel(SAVED_TEXT)
        self.saved.setObjectName("saved")
        self.saved.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.saved)
        for icon, label, action in (
            ("history", "Old pages", self.show_history),
            ("new", "New page", self.new_page),
            ("settings", "Make it yours", self.toggle_panel),
        ):
            button = self.button(label, action)
            button.setIcon(action_icon(icon, "#14213D"))
            header.addWidget(button)
            if icon == "history":
                self.history_button = button
            if icon == "settings":
                self.panel_button = button
                button.setCheckable(True)
        main.addWidget(bar)

        body = QHBoxLayout()
        self.paper = QFrame()
        self.paper.setObjectName("paper")
        paper_layout = QVBoxLayout(self.paper)
        paper_layout.setContentsMargins(26, 24, 26, 19)
        self.paper_shadow = shadow(self.paper, alpha=46, blur=46, offset=14)
        self.editor = Paper()
        self.editor.setAcceptRichText(False)
        self.editor.setTabChangesFocus(True)
        self.editor.document().setDocumentMargin(22)
        self.editor.setAccessibleName("Writing area / Fejn tikteb")
        self.editor.setToolTip("Write here. Ctrl+W opens word actions. / Ikteb hawn. Ctrl+W jiftaħ l-għażliet tal-kelma.")
        self.editor.tapped.connect(self.tap_word)
        self.editor.typed_boundary.connect(self.autofix_word)
        self.editor.sentence_finished.connect(self.read_sentence)
        self.language_box.currentIndexChanged.connect(lambda: self.apply_helpers())
        paper_layout.addWidget(self.editor)
        self.phrases = QWidget()
        self.phrase_layout = QVBoxLayout(self.phrases)
        self.phrases.hide()
        paper_layout.addWidget(self.phrases)
        self.word_count = QLabel(alignment=Qt.AlignmentFlag.AlignRight)
        self.word_count.setObjectName("wordCount")
        paper_layout.addWidget(self.word_count)
        self.card = QFrame(self.editor.viewport())
        self.card.setObjectName("wordCard")
        shadow(self.card)
        self.card_layout = QGridLayout(self.card)
        self.card.hide()
        self.editor.verticalScrollBar().valueChanged.connect(lambda: self.close_card())
        QShortcut(QKeySequence("Ctrl+W"), self.editor, self.tap_word)
        QShortcut(QKeySequence("Esc"), self, self.dismiss_inline)
        body.addWidget(self.paper, 1)
        side = QVBoxLayout()
        self.bubble = SpeechBubble()
        shadow(self.bubble)
        self.bubble.setObjectName("padBubble")
        self.bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bubble.setWordWrap(True)
        self.bubble.setMinimumHeight(60)
        self.bubble.setFixedWidth(156)
        self.face = QLabel()
        self.face.setFixedSize(120, 120)
        side.addWidget(self.bubble)
        side.addWidget(self.face, alignment=Qt.AlignmentFlag.AlignHCenter)
        side.addStretch()
        body.addLayout(side)
        main.addLayout(body, 1)
        self.mood("rest", "Ikteb jew tkellem.")

        self.dock = QFrame()
        self.dock.setObjectName("padDock")
        shadow(self.dock)
        toolbar = QHBoxLayout(self.dock)
        toolbar.setContentsMargins(18, 10, 18, 10)
        toolbar.setSpacing(8)
        for icon, label, action, colours in (
            ("speak", "Speak\nTkellem", lambda: self.dictate("speak"), ("#76E8FA", "#0FA3C9")),
            ("read", "Read\nAqra", self.toggle_read, ("#80EDC1", "#1F9E6E")),
            ("fix", "Fix letters\nIrranġa", self.fix, ("#FFD978", "#F29A0B")),
            ("help", "Help me\nGħinni", lambda: self.dictate("help"), ("#C2A6FF", "#7247E8")),
            ("paste", "Put it in the chat\nDaħħlu fiċ-chat", self.put_in_chat, None),
        ):
            if icon == "paste":
                divider = QFrame()
                divider.setFixedSize(1, 70)
                divider.setStyleSheet("background:rgba(20,33,61,31);")
                toolbar.addWidget(divider)
            button = DockAction(label, action_icon(icon), colours)
            button.clicked.connect(action)
            toolbar.addWidget(button)
            if icon == "read":
                self.read_button = button
        main.addWidget(self.dock, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.build_settings()

    def build_settings(self):
        self.panel = QScrollArea(self)
        self.panel.hide()
        self.panel.setObjectName("padPanel")
        self.panel.setWidgetResizable(True)
        self.panel.setFrameShape(QFrame.Shape.NoFrame)
        self.panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.panel.setViewportMargins(1, 8, 1, 8)
        self.panel_animation = QPropertyAnimation(self.panel, b"pos", self)
        self.panel_animation.setDuration(230)
        self.panel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.panel_animation.finished.connect(self.finish_panel_animation)
        shadow(self.panel, alpha=125, blur=50, offset=12)
        content = QWidget()
        content.setObjectName("padOptions")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(11)
        header = QHBoxLayout()
        heading = QLabel("Make it yours")
        heading.setStyleSheet("font-size:24px;font-weight:800;")
        header.addWidget(heading)
        header.addStretch()
        self.panel_close = self.button("", self.toggle_panel)
        self.panel_close.setIcon(action_icon("close", "#14213D"))
        self.panel_close.setToolTip("Close settings")
        self.panel_close.setAccessibleName("Close settings")
        header.addWidget(self.panel_close)
        layout.addLayout(header)
        layout.addWidget(QLabel("Agħmilha tiegħek · changes straight away"))
        self.choices = {}
        self.switches = {}
        for title, name, values, columns in (
            ("Colours · Kuluri", "theme", THEMES, 6),
            ("Letters · Ittri", "font", FONTS, 2),
            ("Size · Daqs", "size", (), 0),
            ("Space · Spazju", "spacing", SPACES, 3),
            ("Reading colour · Kulur tal-qari", "mark", MARKS, 4),
            ("Helpers · Għajnuna", "helpers", (), 0),
            ("Pad name · Isem il-karta", "name", NAMES, 2),
        ):
            label = QLabel(title.upper())
            label.setStyleSheet("color:#626D85;font-size:12px;font-weight:800;")
            title_row = QHBoxLayout()
            title_row.addWidget(label)
            layout.addLayout(title_row)
            if name == "size":
                self.size_label = QLabel()
                self.size_label.setStyleSheet("color:#5159DA;font-weight:800;")
                title_row.addStretch()
                title_row.addWidget(self.size_label)
                self.size_slider = SizeSlider(Qt.Orientation.Horizontal)
                self.size_slider.setRange(18, 48)
                self.size_slider.setMinimumHeight(30)
                self.size_slider.setAccessibleName(title)
                self.size_slider.valueChanged.connect(lambda value: self.set_option("size", value))
                layout.addWidget(self.size_slider)
                continue
            if name == "helpers":
                for key, icon, english, maltese in (
                    ("ruler", "ruler", "Reading ruler", "Riga li timxi mal-qari"),
                    ("autofix", "fix", "Fix letters as I write", "Irranġa l-ittri waqt li nikteb"),
                    ("followRead", "read", "Read each sentence when I finish it", "Aqra kull sentenza meta nispiċċaha"),
                    ("bigBuddy", "buddy", "Big buddy", "Il-ħabib kbir"),
                ):
                    row = QFrame()
                    row.setObjectName("helperRow")
                    items = QHBoxLayout(row)
                    symbol = QLabel()
                    symbol.setPixmap(action_icon(icon, "#5867AE").pixmap(21, 21))
                    items.addWidget(symbol)
                    caption = QLabel(f"<b>{english}</b><br><small>{maltese}</small>")
                    caption.setWordWrap(True)
                    items.addWidget(caption, 1)
                    switch = ToggleSwitch(self.options[key])
                    switch.setAccessibleName(english)
                    switch.toggled.connect(lambda value, k=key: self.set_option(k, value))
                    self.switches[key] = switch
                    items.addWidget(switch)
                    layout.addWidget(row)
                continue
            group = QFrame()
            if name in ("spacing", "name"):
                group.setObjectName("segment")
            grid = QGridLayout(group)
            grid.setContentsMargins(4, 4, 4, 4)
            grid.setSpacing(6)
            layout.addWidget(group)
            for index, value in enumerate(values):
                text = "Atkinson" if value == "Atkinson Hyperlegible" else value
                if name == "theme":
                    button = Swatch(54, THEMES[value][5], checkmark=True,
                                    ink="white" if value in ("Night", "Bold") else "#293359")
                elif name == "mark":
                    button = Swatch(42, (MARKS[value],) * 2)
                else:
                    button = QPushButton(text)
                button.clicked.connect(lambda checked=False, k=name, v=value: self.set_option(k, v))
                button.setToolTip(f"{title} · {value}")
                button.setAccessibleName(f"{title} · {value}")
                if name in ("theme", "mark"):
                    grid.addWidget(button, 0, index, alignment=Qt.AlignmentFlag.AlignHCenter)
                    if name == "theme":
                        caption = QLabel(value, alignment=Qt.AlignmentFlag.AlignHCenter)
                        caption.setStyleSheet("font-size:11px;font-weight:700;")
                        grid.addWidget(caption, 1, index)
                else:
                    if name == "font":
                        button.setText("")
                        sample_layout = QVBoxLayout(button)
                        sample_layout.setContentsMargins(12, 12, 12, 12)
                        sample_layout.setSpacing(4)
                        sample = QLabel("Għandek raġun")
                        sample.setFont(QFont(value if value in self.fonts else "Segoe UI", 13, QFont.Weight.Bold))
                        caption = QLabel(text)
                        for line in (sample, caption):
                            line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                            sample_layout.addWidget(line)
                    grid.addWidget(button, index // columns, index % columns)
                self.choices[name, value] = button
        layout.addStretch()
        self.panel.setWidget(content)
        self.panel.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self.panel.setVisible(self.options["panel"])
        self.panel.raise_()

    def set_option(self, name, value):
        if self.options[name] == value:
            return
        self.options[name] = value
        self.host.change("pad", self.options)
        if name == "size":
            self.update_size_label()
            self.apply_editor_font()
        elif name == "panel":
            self.animate_panel()
        elif name == "bigBuddy":
            self.draw_buddy()
        elif name in ("ruler", "autofix", "followRead"):
            self.apply_helpers()
        else:
            self.apply_style(space=name == "spacing")
        if name in self.switches:
            self.switches[name].setChecked(value)

    def toggle_panel(self):
        self.set_option("panel", not self.options["panel"])

    def panel_position(self):
        layout = self.layout()
        return QPoint(self.width() - PANEL_WIDTH - layout.contentsMargins().right(),
                      layout.itemAt(0).widget().geometry().bottom() + 1 + layout.spacing())

    def arrange_panel(self, showing):
        layout = self.layout()
        target = self.panel_position()
        side_by_side = target.x() - layout.contentsMargins().left() >= self.dock.sizeHint().width() + PANEL_GAP
        layout.setAlignment(self.dock, Qt.AlignmentFlag.AlignLeft if showing and side_by_side
                            else Qt.AlignmentFlag.AlignHCenter)
        layout.activate()
        bottom = self.height() - layout.contentsMargins().bottom()
        if not side_by_side:
            bottom = self.dock.y() - layout.spacing()
        self.panel.resize(PANEL_WIDTH, max(0, bottom - target.y()))
        self.panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "panel_animation"):
            return
        self.panel_animation.stop()
        self.arrange_panel(self.options["panel"])
        self.panel.move(self.panel_position())
        self.panel.setVisible(self.options["panel"])
        self.panel.raise_()

    def animate_panel(self):
        opened = self.options["panel"]
        self.panel_button.setChecked(opened)
        self.panel_button.setIcon(action_icon("settings", "white" if opened else "#14213D"))
        self.panel_animation.stop()
        self.arrange_panel(True)
        target = self.panel_position()
        outside = QPoint(self.width() + 8, target.y())
        if self.panel.isHidden():
            self.panel.move(outside)
            self.panel.show()
        self.panel.raise_()
        self.panel_animation.setStartValue(self.panel.pos())
        self.panel_animation.setEndValue(target if opened else outside)
        self.panel_animation.start()

    def finish_panel_animation(self):
        if self.options["panel"]:
            self.panel_close.setFocus()
        else:
            self.panel.hide()
            self.arrange_panel(False)
            self.panel_button.setFocus()

    def advance_background(self):
        self.drift_step = (self.drift_step + 1) % 140
        self.update()

    def paintEvent(self, event):
        bg, _, _, _, blobs, _, _ = THEMES[self.options["theme"]]
        width, height = self.width(), self.height()
        phase = self.drift_step * (6.283185307 / 140)
        with QPainter(self) as painter:
            painter.fillRect(event.rect(), QColor(bg))
            for index, (x, y, radius) in enumerate(((140, 120, 310), (width - 145, height * .55, 295),
                                                     (width * .44, height + 15, 255))):
                tint = QColor(blobs[index])
                center = QPointF(x + 28 * sin(phase + index * 2), y - 20 * sin(phase + index * 2))
                fill = QRadialGradient(center, radius)
                tint.setAlpha(178)
                fill.setColorAt(0, tint)
                tint.setAlpha(102)
                fill.setColorAt(.57, tint)
                tint.setAlpha(0)
                fill.setColorAt(1, tint)
                painter.fillRect(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2), fill)

    def apply_style(self, *, space=True):
        theme = self.options["theme"]
        _, paper, fg, title, _, _, tint = THEMES[theme]
        dark = theme in ("Night", "Bold")
        header_fill = HEADER_DARK if dark else HEADER_LIGHT
        dock_fill = "rgba(34,43,67,236)" if dark else "rgba(255,255,255,155)"
        colour = QColor(tint)
        colour.setAlpha(105 if theme == "Night" else 46)
        self.paper_shadow.setColor(colour)
        self.setStyleSheet(f"""
            #padHeader, #padDock {{
                border:1px solid rgba(255,255,255,214);border-radius:24px;}}
            #padHeader {{background:{header_fill};}}
            #padDock {{background:{dock_fill};border-radius:38px;}}
            #padTitle {{font-size:32px;font-weight:800;padding-right:4px;}}
            #saved {{color:#155E45;background:rgba(255,255,255,172);border-radius:13px;
                padding:4px 8px;font-weight:700;}}
            #paper {{background:{paper};border-radius:36px;border:1px solid rgba(255,255,255,200);}}
            QTextEdit {{background:transparent;color:{fg};border:0;
                selection-background-color:#1961a8;selection-color:white;}}
            #wordCount {{color:{fg};font-weight:700;padding-right:8px;}}
            #padBubble {{padding:10px;margin-bottom:8px;font-weight:700;}}
            #wordCard {{background:rgba(255,255,255,225);color:#14213D;
                border:1px solid white;border-radius:24px;}}
            #padPanel {{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,249),stop:1 rgba(255,255,255,240));
                border:1px solid rgba(255,255,255,250);
                border-radius:32px;}}
            #padPanel > QWidget > QWidget, #padOptions {{background:transparent;}}
            #padPanel QScrollBar:vertical {{background:#DFE4EF;width:12px;margin:16px 3px 16px 0;border-radius:4px;}}
            #padPanel QScrollBar::handle:vertical {{background:#667394;border-radius:4px;min-height:32px;}}
            #padPanel QScrollBar::add-line, #padPanel QScrollBar::sub-line {{height:0;}}
            #padPanel QScrollBar::add-page, #padPanel QScrollBar::sub-page {{background:transparent;}}
            #padOptions QLabel, #wordCard QLabel {{color:#14213D;}}
            #segment, #helperRow {{background:rgba(20,33,61,14);border-radius:16px;}}
            QPushButton, QComboBox {{background:rgba(255,255,255,203);color:#14213D;
                border:1px solid rgba(255,255,255,230);border-radius:19px;
                padding:6px 12px;min-height:24px;font-weight:700;}}
            QPushButton:hover, QComboBox:hover {{background:white;border-color:#B6C5EA;}}
            QPushButton:focus, QComboBox:focus {{border-color:#4650CE;}}
            #padHeader QPushButton:checked {{background:{CHIP_GRADIENT};color:white;}}
            QComboBox {{padding-left:6px;padding-right:20px;}}
            QComboBox::drop-down {{width:17px;border:0;}}
            QSlider::groove:horizontal {{height:8px;background:transparent;}}
            QSlider::handle:horizontal {{width:24px;margin:-8px 0;background:transparent;}}
            QMenu {{background:#FAFBFF;color:#14213D;border:1px solid #D5DCEF;border-radius:16px;padding:8px;}}
            QListWidget {{background:#FAFBFF;color:#14213D;border:0;outline:0;}}
            QListWidget::item {{padding:10px;border-radius:8px;}}
            QListWidget::item:selected {{background:#E3E8FF;color:#14213D;}}
        """)
        self.setWindowTitle(self.options["name"])
        self.title.colours = title
        self.title.setText(self.options["name"])
        self.title.update()
        for button in self.dock.findChildren(DockAction):
            button.dark = dark
        self.panel_button.setChecked(self.options["panel"])
        self.panel_button.setIcon(action_icon("settings", "white" if self.options["panel"] else "#14213D"))
        self.update_size_label()
        self.apply_helpers()
        self.update()
        self.apply_editor_font()
        if space:
            height = SPACES[self.options["spacing"]][0]
            cursor = QTextCursor(self.editor.document())
            cursor.select(QTextCursor.SelectionType.Document)
            block = QTextBlockFormat()
            block.setLineHeight(height, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            self.formatting = True
            cursor.mergeBlockFormat(block)
            self.formatting = False
        for (name, value), button in self.choices.items():
            selected = self.options[name] == value
            if name in ("theme", "mark"):
                button.setChecked(selected)
            else:
                background = ("#EEF0FF" if name == "font" else "white") if selected else (
                    "rgba(20,33,61,13)" if name == "font" else "transparent")
                ring = "#5B63FF" if selected and name == "font" else "transparent"
                ink = "#4650CE" if selected else "#14213D"
                sizing = "padding:0;min-height:64px;" if name == "font" else "padding:4px;"
                button.setStyleSheet(f"""
                    QPushButton {{background:{background};color:{ink};border:2px solid {ring};
                        border-radius:12px;{sizing}}}
                    QPushButton:hover, QPushButton:focus {{border-color:#8E99E2;}}
                """)
                if name == "font":
                    button.setMinimumSize(button.minimumSize().expandedTo(button.layout().minimumSize()))
        self.paint_marks()

    def apply_editor_font(self):
        _, letter, word = SPACES[self.options["spacing"]]
        family = self.options["font"]
        font = QFont(family if family in self.fonts else "Segoe UI")
        font.setPixelSize(self.options["size"])
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter)
        font.setWordSpacing(word)
        self.editor.document().setDefaultFont(font)

    def apply_helpers(self):
        self.editor.ruler = self.options["ruler"]
        self.editor.autofix = self.options["autofix"] and self.language == "mt"
        self.editor.follow_read = self.options["followRead"]
        self.editor.viewport().update()

    def update_size_label(self):
        size = self.options["size"]
        self.size_label.setText(f"{size} px")
        self.size_slider.setValue(size)

    def update_drift(self):
        if self.isVisible() and not self.isMinimized():
            self.drift.start()
        else:
            self.drift.stop()

    def hideEvent(self, event):
        self.drift.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.update_drift()

    def showEvent(self, event):
        super().showEvent(event)
        self.autosave.start()
        self.update_drift()
        self.editor.setFocus()
        self.editor.ensureCursorVisible()

    def closeEvent(self, event):
        if not self.save_page():
            event.ignore()
            return
        self.autosave.stop()
        self.stop_read()
        self.dismiss_inline()
        self.host.cancel_pad_capture()
        self.options["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.host.change("pad", self.options)
        super().closeEvent(event)

    def mood(self, mood, message, *, brief=False):
        self.current_mood = mood
        self.draw_buddy()
        self.bubble.setText(message)
        if hasattr(self, "mood_timer"):
            self.mood_timer.stop()
            if brief:
                self.mood_timer.start()

    def draw_buddy(self):
        extent = 180 if self.options["bigBuddy"] else 120
        self.face.setFixedSize(extent, extent)
        self.bubble.setFixedWidth(186 if self.options["bigBuddy"] else 156)
        svg = self.art[self.current_mood].replace(
            'width="104" height="104"', f'width="{extent}" height="{extent}"', 1)
        self.face.setPixmap(svg_pixmap(svg).scaled(
            extent, extent, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def update_count(self):
        count = sum(1 for _ in WORD.finditer(self.editor.toPlainText()))
        self.word_count.setText(f"{count} words · kliem")

    def autofix_word(self, position):
        if not self.options["autofix"] or self.language != "mt":
            return
        caret = self.selection(position, position)
        prefix = self.selection(caret.block().position(), position).selectedText()
        word = next((match[0] for match in reversed(list(WORD.finditer(prefix)))
                     if match.end() == len(prefix)), "")
        if not word:
            return
        try:
            fixed = fix_letters(word)
        except Exception:
            logging.exception("Pad could not fix the typed word")
            return
        if fixed != word:
            cursor = self.selection(position - qt_position(word, len(word)), position)
            cursor.insertText(fixed)
            self.editor.setTextCursor(cursor)

    def read_sentence(self):
        if not self.options["followRead"]:
            return
        end = self.editor.textCursor().position()
        block_start = self.editor.textCursor().block().position()
        prefix = self.selection(block_start, end).selectedText()
        match = re.search(r"[^.!?\u2029]+[.!?]$", prefix)
        if match and WORD.search(match[0]):
            start = match.start() + len(match[0]) - len(match[0].lstrip())
            self.read(self.selection(block_start + qt_position(prefix, start), end), automatic=True)

    def show_history(self):
        menu = QMenu(self)
        self.history_menu = menu
        listing = QListWidget()
        self.history_list = listing
        listing.setAccessibleName("Old pages")
        listing.setFixedWidth(330)
        paths = sorted(self.pages.history.glob("*.txt"), reverse=True)
        for path in paths:
            try:
                page = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            preview = " ".join(page.split())
            label = preview[:68] + ("…" if len(preview) > 68 else "")
            item = QListWidgetItem(label or "Blank page")
            item.setData(Qt.ItemDataRole.UserRole, page)
            listing.addItem(item)
        if not listing.count():
            item = QListWidgetItem("No old pages yet")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            listing.addItem(item)
        listing.setFixedHeight(min(300, listing.count() * 46 + 5))

        def load(item):
            page = item.data(Qt.ItemDataRole.UserRole)
            if page is None or not self.save_page():
                return
            self.dismiss_inline()
            self.host.cancel_pad_capture()
            self.editor.setPlainText(page)
            self.apply_style()
            self.editor.document().clearUndoRedoStacks()
            self.save_page()
            menu.close()
            self.editor.setFocus()

        listing.itemClicked.connect(load)
        listing.itemActivated.connect(load)
        action = QWidgetAction(menu)
        action.setDefaultWidget(listing)
        menu.addAction(action)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.history_button.mapToGlobal(QPoint(0, self.history_button.height())))
        listing.setFocus()
        if paths and listing.item(0).flags() & Qt.ItemFlag.ItemIsEnabled:
            listing.setCurrentRow(0)

    def edited(self):
        if getattr(self, "formatting", False):
            return
        followed = self.followed_sentence
        if followed is None or followed[1] >= self.editor.document().characterCount():
            self.stop_read()
        elif self.selection(*followed[:2]).selectedText() != followed[2]:
            self.stop_read()
        self.clear_flash()
        self.close_card()
        self.update_count()
        self.saved.setText("Saving…")
        self.saved.setToolTip("Saving… · Qed nissejvja…")

    def save_page(self, *, new_page=False):
        try:
            self.pages.save(self.editor.toPlainText(), new_page=new_page)
        except (OSError, UnicodeError):
            logging.exception("Could not save Kelma Pad")
            self.saved.setText("Save failed")
            self.saved.setToolTip("Save failed · Ma ssejvjax")
            return False
        self.saved.setText(SAVED_TEXT)
        self.saved.setToolTip("")
        return True

    def new_page(self):
        if self.save_page(new_page=True):
            self.dismiss_inline()
            self.host.cancel_pad_capture()
            self.editor.clear()
            self.apply_style()
            self.editor.document().clearUndoRedoStacks()
            self.saved.setText(SAVED_TEXT)
            self.saved.setToolTip("")
            self.editor.setFocus()

    def selection(self, start, end):
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def insert(self, cursor, text):
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        self.editor.ensureCursorVisible()

    def fix(self):
        if self.language != "mt":
            self.mood("rest", "Choose MT to fix Maltese letters. / Agħżel MT għall-ittri Maltin.")
            return
        before = self.editor.toPlainText()
        try:
            after = fix_letters(before)
        except Exception:
            logging.exception("Pad spelling correction failed")
            self.mood("oops", "Could not fix the letters. / Ma stajtx nirranġa l-ittri.")
            return
        if before == after:
            self.mood("rest", "No changes. / Bla tibdil.")
            return
        old_words, new_words = list(WORD.finditer(before)), list(WORD.finditer(after))
        diff = SequenceMatcher(a=[m[0] for m in old_words], b=[m[0] for m in new_words], autojunk=False)
        spans = [(qt_position(after, m.start()), qt_position(after, m.end()))
                 for tag, _, _, start, end in diff.get_opcodes() if tag != "equal"
                 for m in new_words[start:end]]
        caret = self.editor.textCursor().position()
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.beginEditBlock()
        cursor.insertText(after)
        cursor.endEditBlock()
        cursor.setPosition(min(caret, self.editor.document().characterCount() - 1))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        self.editor.ensureCursorVisible()
        self.flash = spans
        self.paint_marks()
        self.flash_timer.start()
        self.mood("party", "Irranġajt l-ittri!", brief=True)

    def clear_flash(self):
        if self.flash:
            self.flash = []
            self.paint_marks()

    def paint_marks(self):
        spans = [(a, b, MARKS["Green"]) for a, b in self.flash]
        self.editor.reading_mark = None
        if self.read_span:
            colour = MARKS[self.options["mark"]]
            spans.append((*self.read_span, colour))
            self.editor.reading_mark = (self.selection(*self.read_span), colour)
        selections = []
        for start, end, colour in spans:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = self.selection(start, end)
            selection.format.setBackground(QColor(colour))
            selection.format.setForeground(QColor("#10233D"))
            selections.append(selection)
        self.editor.setExtraSelections(selections)
        self.editor.viewport().update()

    def toggle_read(self):
        if self.reading:
            self.stop_read()
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            caret = QTextCursor(cursor)
            caret.clearSelection()
            self.editor.setTextCursor(caret)
        else:
            cursor.select(QTextCursor.SelectionType.Document)
        self.read(cursor)

    def read(self, cursor, *, automatic=False):
        text = cursor.selectedText().replace("\u2029", "\n")
        if not text.strip():
            return
        start, language = cursor.selectionStart(), self.language
        self.stop_read()
        self.close_card()
        stop = self.reading = Event()
        if automatic:
            self.followed_sentence = (start, cursor.selectionEnd(), cursor.selectedText())
        self.read_button.setText("Stop\nWaqqaf")
        self.read_button.setIcon(action_icon("stop"))
        self.read_button.setToolTip("Stop / Waqqaf")
        self.mood("read", "Qed naqra…")

        def work():
            try:
                # Same natural voices as the rest of Il-Kelma (mt-MT Grace etc.) when online voices
                # are on; Windows has no good Maltese voice. Falls back to Windows voices offline.
                play_words(text, language, stop, lambda a, b: self.boundary.emit(
                    stop, start + qt_position(text, a), start + qt_position(text, b)),
                    online=bool(self.host.settings.get("voices", True)))
                return True
            except Exception:
                logging.exception("Kelma Pad could not read aloud")
                return False

        def finished(ok):
            if self.reading is stop:
                self.stop_read()
                if not ok:
                    self.mood("oops", "Could not read aloud. / Ma stajtx naqra.")

        self.host.run(work, finished)

    def highlight_word(self, stop, start, end):
        if self.reading is stop and not stop.is_set():
            self.read_span = (start, end)
            self.paint_marks()
            rect = self.editor.cursorRect(self.selection(start, start))
            if not self.editor.viewport().rect().contains(rect):
                bar = self.editor.verticalScrollBar()
                bar.setValue(bar.value() + rect.top() - self.editor.viewport().height() // 3)

    def stop_read(self):
        if self.reading:
            self.reading.set()
            self.reading = self.read_span = self.followed_sentence = None
            self.paint_marks()
            self.read_button.setText("Read\nAqra")
            self.read_button.setIcon(action_icon("read"))
            self.read_button.setToolTip("Read / Aqra")
            self.mood("rest", "Ikteb jew tkellem.")

    def tap_word(self, point=None):
        cursor = self.editor.cursorForPosition(point) if point is not None else self.editor.textCursor()
        text = self.editor.toPlainText()
        position = cursor.position()
        for match in WORD.finditer(text):
            start, end = (qt_position(text, i) for i in match.span())
            if start <= position <= end:
                if point is not None:
                    left = self.editor.cursorRect(self.selection(start, start))
                    right = self.editor.cursorRect(self.selection(end, end))
                    if left.top() == right.top() and not (left.left() <= point.x() <= right.right()):
                        continue
                self.show_card(match[0], self.selection(start, end), keyboard=point is None)
                return
            if start > position:
                break
        self.close_card()

    def show_card(self, word, cursor, *, keyboard=False):
        self.close_card()
        self.picked = cursor
        label = QLabel(word)
        label.setTextFormat(Qt.TextFormat.PlainText)
        self.card_layout.addWidget(label, 0, 0)
        actions = (
            ("read", "Isma’", "Hear word / Isma’ l-kelma", lambda: self.read(cursor)),
            ("speak", "Erġa’ għidha", "Say it again / Erġa’ għidha", lambda: self.dictate("speak", cursor)),
            ("close", "", "Close word actions / Agħlaq l-għażliet", lambda: self.close_card(focus=True)),
        )
        first = None
        for index, (icon, label, tip, action) in enumerate(actions, 1):
            button = self.button(label, action)
            if icon:
                button.setIcon(action_icon(icon, "#10233D"))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            self.card_layout.addWidget(button, 0, index)
            first = first or button
        try:
            alternatives = suggestions(word, self.language)
        except (OSError, ValueError, RuntimeError):
            logging.exception("Could not find Pad word suggestions")
            alternatives = []
        for index, alternate in enumerate(alternatives):
            button = self.button(alternate, lambda value=alternate: self.insert(cursor, value))
            if index == 0:
                button.setIcon(action_icon("fix", "#7359C8"))
            button.setToolTip("Replace word / Ibdel il-kelma")
            self.card_layout.addWidget(button, 1, index)
        rect = self.editor.cursorRect(cursor)
        viewport = self.editor.viewport()
        self.card.adjustSize()
        x = max(0, min(rect.left(), viewport.width() - self.card.width()))
        y = rect.bottom() + 4
        if y + self.card.height() > viewport.height():
            y = max(0, rect.top() - self.card.height() - 4)
        self.card.move(x, y)
        self.card.show()
        self.card.raise_()
        if keyboard:
            first.setFocus()

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()

    def close_card(self, *, focus=False):
        self.card.hide()
        self.clear_layout(self.card_layout)
        self.picked = None
        if focus:
            self.editor.setFocus()

    def dismiss_inline(self):
        self.close_card(focus=self.card.isVisible())
        self.phrases.hide()
        self.clear_layout(self.phrase_layout)

    def dictate(self, mode, cursor=None):
        self.stop_read()
        self.dismiss_inline()
        replacing = cursor is not None
        destination = cursor if replacing else self.editor.textCursor()
        original_word = destination.selectedText() if replacing else ""
        if not replacing:
            destination.clearSelection()
        language = self.language

        def insert(text):
            if replacing:
                if destination.selectedText() != original_word:
                    self.mood("oops", "The word changed. / Il-kelma nbidlet.")
                    return
                text = text.strip().rstrip(".!?")
                if original_word and text and original_word[0].islower():
                    text = text[0].lower() + text[1:]
            elif text:
                document = self.editor.document()
                position = destination.position()
                left = document.characterAt(position - 1) if position else ""
                right = document.characterAt(position)
                if text[0].isalnum() and left and (left.isalnum() or left in ".,!?;:"):
                    text = " " + text
                if right and right.isalnum() and not text[-1].isspace():
                    text += " "
            if text:
                self.insert(destination, text)
                self.mood("done", "Daħħalt it-test.", brief=True)

        def choose(idea, options):
            for option in options:
                def accept(text=option.text):
                    confirm_phrase(idea, language, text)
                    self.dismiss_inline()
                    insert(text)

                self.phrase_layout.addWidget(self.button(option.text, accept))
            self.phrase_layout.addWidget(self.button("Cancel / Ikkanċella", self.dismiss_inline))
            self.phrases.show()
            self.phrase_layout.itemAt(0).widget().setFocus()
            self.mood("idea", "Choose a sentence. / Agħżel sentenza.")

        self.host.pad_capture(mode, language, choose if mode == "help" else insert)

    def put_in_chat(self):
        text = self.editor.toPlainText()
        if text.strip():
            self.host.paste_pad(text)
