from dataclasses import replace
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Event, Thread
from time import monotonic
import win32gui
import winsound

from PySide6.QtCore import QLockFile, QPoint, QRect, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtMultimedia import QMediaDevices, QAudio
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QProgressBar, QPushButton, QScrollArea, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from ilkelma.api import start
from ilkelma.engine.audio import (
    SOUND_LEVEL, Recorder, fallback_microphone, legacy_microphone, loudest_microphone, microphones,
)
from ilkelma.engine.language import detect
from ilkelma.engine.learned_words import WordStore, tokens
from ilkelma.engine.listen import SESSION_SECONDS, listen
from ilkelma.engine.phrasing import DEFAULT_MODEL
from ilkelma.engine.stt import Engine
from ilkelma.engine.tts import announce, play
from ilkelma.engine.windows import Desktop, ForegroundTracker, screen_origins
from ilkelma.languages import buddy_message, chosen, packs, set_chosen

from .hotkeys import (
    COMMENTS_FALLBACKS, HELP_FALLBACKS, KEYS, POINT_FALLBACKS, READ_FALLBACKS, Hotkeys, bind_startup,
)
from .learning import apply_corrections, confirm_phrase, corrected, learn_word, phrase_key, spoken_option, suggest_options
from .pad import Pad
from .screen_reader import CaptureExclusion, ScreenReader
from .storage import DEFAULTS, load, save
from .buddy import Buddy, SIZE, feedback, mood_for, next_language, reaction
from .widgets import TOOL, buddy_icon

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ("Click Buddy for Write, Read and Help me say it. Click in a typing box, "
         "then press Buddy's clip to keep it there.")
WRITE_FALLBACKS = ("F9", "F7", "F6")


def buddy_position(box, area, width, height):
    gap = 8
    x = box.right() + gap
    y = box.center().y() - height // 2
    if x + width > area.right():
        x = box.right() - width
        y = box.top() - height - gap
        if y < area.top():
            y = box.bottom() + gap
    return QPoint(max(area.left(), min(x, area.right() - width)),
                  max(area.top(), min(y, area.bottom() - height)))


class Completion(QObject):
    done = Signal(object, object)
    listening_done = Signal(str)


class Window(QMainWindow):
    def __init__(self, application):
        super().__init__()
        self.application = application
        self.settings = DEFAULTS | load("settings", {})
        set_chosen(self.settings["my_languages"])
        if self.settings["language"] and self.settings["language"] not in chosen():
            self.settings["language"] = ""
        self.history = load("history", [])[:20]
        backend = self.settings["engine"]
        self.engine = Engine(backend=backend if backend in ("whisper", "parakeet") else "whisper")
        self.desktop = Desktop(int(self.winId()))
        self.foreground_tracker = ForegroundTracker(self.desktop)
        self.test_audio = os.environ.get("ILKELMA_TEST_AUDIO") or self.settings.get("test_audio")
        self.devices = {} if self.test_audio else microphones()
        self.preferred = self.settings.get("mic_id")
        self.peaks = {}
        self.probes = None
        self.active_mic = self.recording_mic = None
        self.preferred_missing = False
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.completion = Completion()
        self.completion.done.connect(self.completed)
        self.completion.listening_done.connect(self.listen_finished)
        self.listen_stop = None
        self.listen_slot = object()
        self.listen_timer = QTimer(self, singleShot=True, interval=SESSION_SECONDS * 1000)
        self.listen_timer.timeout.connect(self.listen_timed_out)
        self.state = "idle"
        self.stop_audio = Event()
        self.stop_bubble_audio = Event()
        self.recorder = self.preview = None
        self.capture_mode = "write"
        self.pad = self.pad_job = self.pad_target = None
        self.pending_phrase = None
        self.phrase_audio = Event()
        self.capture_target = self.clipped = None
        self.quitting = False
        self.icon = buddy_icon()
        self.setWindowIcon(self.icon)
        self.setWindowTitle("Il-Kelma · Speak & Write")
        self.build_window()
        self.buddy = Buddy(self.settings["buddy_name"], self.settings["language"])
        self.screen_reader = ScreenReader(self)
        self.buddy.action.connect(self.buddy_action)
        self.buddy.moved.connect(self.save_buddy_position)
        saved = self.settings.get("buddy_position") or self.settings.get("dot")
        if saved and QApplication.screenAt(QPoint(*saved) + QPoint(52, 52)):
            self.buddy.set_position(QPoint(*saved))
        self.clip_rect = None
        self.build_phrase_preview()
        self.build_tray()
        self.buddy.show()
        self.clip_timer = QTimer(self, interval=650)
        self.clip_timer.timeout.connect(self.track_clip)
        self.clip_timer.timeout.connect(self.track_pad_target)
        self.clip_timer.start()
        self.track_clip(startup=True)
        self.bubble("greeting", user=self.settings["user_name"], buddy=self.settings["buddy_name"])
        self.hotkeys = Hotkeys(self, {
            1: lambda: self.after_chat_selection(self.toggle),
            2: lambda: self.after_chat_selection(self.read_selection),
            3: self.cancel,
            4: lambda: self.after_chat_selection(self.help_with_phrase),
            5: lambda: self.screen_reader.point(immediate=True),
            6: self.screen_reader.cancel_selection,
            7: self.screen_reader.toggle_comments,
            8: lambda: self.after_chat_selection(self.open_pad),
        })
        application.installNativeEventFilter(self.hotkeys)
        shortcut_notice = self.register_shortcuts()
        comments_key = bind_startup(self.hotkeys, 7, "F6", COMMENTS_FALLBACKS)
        self.comments_key_label.setText(comments_key or "Use Buddy’s Read comments button")
        help_notice = self.register_help_shortcut()
        point_key = bind_startup(self.hotkeys, 5, "F7", POINT_FALLBACKS)
        self.point_key_label.setText(point_key or "Use Buddy’s right-click menu")
        pad_key = bind_startup(self.hotkeys, 8, "F4", ("Ctrl+Alt+K", "F5"))
        self.pad_action.setToolTip(pad_key or "Open Kelma Pad / Iftaħ Il-Karta")
        shortcut_notice = " ".join(filter(None, (shortcut_notice, help_notice)))
        self.timer = QTimer(self, interval=55)
        self.timer.timeout.connect(self.update_meter)
        self.timer.start()
        try:
            self.api = start(self.engine, online_voices=lambda: self.settings["voices"])
        except OSError:
            self.api = None
            self.ask("api_busy")
        if self.test_audio:
            self.setup.setText("Test audio mode uses a WAV file.")
            self.microphone.setEnabled(False)
            self.test_button.setEnabled(False)
        else:
            self.media_devices = QMediaDevices(self)
            self.media_devices.audioInputsChanged.connect(self.refresh_devices)
            self.apply_devices()
            if not self.settings["mic"] and not self.preferred:
                QTimer.singleShot(0, self.calibrate_mics)
        self.update_mic_display()
        self.refresh_history()
        self.theme()
        if shortcut_notice:
            logging.info("%s", shortcut_notice)
            self.bubble("shortcut_changed")

    def build_window(self):
        root = QWidget()
        root.setObjectName("settingsPage")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        page = QVBoxLayout(root)
        page.setContentsMargins(30, 24, 30, 24)
        page.setSpacing(13)
        title = QLabel("Il-Kelma")
        title.setObjectName("title")
        page.addWidget(title)
        guide = QLabel(GUIDE)
        guide.setWordWrap(True)
        page.addWidget(guide)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        listen = QPushButton("🔊 Read screen")
        listen.clicked.connect(self.read_screen)
        row.addWidget(listen)
        page.addLayout(row)
        form = QFormLayout()
        for setting, label in (("user_name", "Your name"), ("buddy_name", "Your buddy's name")):
            name = QLineEdit(self.settings[setting])
            name.setMaxLength(48)
            name.editingFinished.connect(lambda field=name, key=setting: self.save_name(field, key))
            form.addRow(label, name)
        self.microphone = QComboBox()
        self.microphone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.microphone.setMinimumContentsLength(24)
        self.microphone.currentIndexChanged.connect(self.mic_changed)
        form.addRow("Microphone", self.microphone)
        self.setup = QLabel()
        self.setup.setWordWrap(True)
        self.meter = QProgressBar()
        self.meter.setAccessibleName("Microphone level")
        self.meter.setTextVisible(False)
        self.test_button = QPushButton("🎤 Test mic")
        self.test_button.clicked.connect(self.test_mic)
        meter_row = QHBoxLayout()
        meter_row.addWidget(self.meter, 1)
        meter_row.addWidget(self.test_button)
        form.addRow(self.setup)
        form.addRow(meter_row)
        self.language = QComboBox()
        self.fill_language_box()
        self.language.currentIndexChanged.connect(self.language_changed)
        form.addRow("Speech language", self.language)
        self.my_languages = QListWidget()
        self.my_languages.setAccessibleName("My languages")
        self.my_languages.setToolTip("Tick the languages you use. They go on Buddy's flag.")
        self.my_languages.setMaximumHeight(180)
        for code, pack in packs().items():
            item = QListWidgetItem(f'{pack["name"]}  ·  {pack["english_name"]}')
            item.setData(Qt.ItemDataRole.UserRole, code)
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled if code == "mt" else flags)
            item.setCheckState(Qt.CheckState.Checked if code in chosen() else Qt.CheckState.Unchecked)
            self.my_languages.addItem(item)
        self.my_languages.itemChanged.connect(self.my_languages_changed)
        form.addRow("My languages / Il-lingwi tiegħi", self.my_languages)
        self.backend = QComboBox()
        self.backend.addItem("Whisper", "whisper")
        self.backend.addItem("Parakeet", "parakeet")
        self.backend.setCurrentIndex(self.backend.findData(self.engine.backend))
        self.backend.currentIndexChanged.connect(self.engine_changed)
        form.addRow("Speech engine", self.backend)
        self.point_key_label = QLabel()
        form.addRow("Point and read shortcut", self.point_key_label)
        self.comments_key_label = QLabel()
        form.addRow("Read comments shortcut", self.comments_key_label)
        self.key_boxes = {}
        for slot, setting, label in ((1, "write_key", "Write key"), (2, "read_key", "Read key"),
                                     (4, "help_key", "Help me say it key")):
            combo = QComboBox()
            combo.addItems([key for key in KEYS if key != "Esc"])
            combo.addItem("Unavailable")
            combo.model().item(combo.count() - 1).setEnabled(False)
            combo.setCurrentText(self.settings[setting])
            combo.currentTextChanged.connect(lambda key, s=slot, n=setting: self.key_changed(s, n, key))
            self.key_boxes[slot] = combo
            form.addRow(label, combo)
        page.addLayout(form)
        for setting, label in (("read_back", "Read back after pasting"),
                               ("voices", "Use online voices (sends the text to an online voice service)"),
                               ("buddy_speaks", "Buddy says it out loud"),
                               ("dark", "Soft dark theme")):
            check = QCheckBox(label)
            check.setChecked(self.settings[setting])
            check.toggled.connect(lambda enabled, name=setting: self.change(name, enabled))
            page.addWidget(check)
        heading = QLabel("Reading comments / Qari tal-kummenti")
        heading.setObjectName("settingsSection")
        page.addWidget(heading)
        for setting, label, options in (
            ("comment_language", "Language / Lingwa", (
                ("auto", "Each comment in its own language / Kull kumment bil-lingwa tiegħu"),
                ("mt", "Always Maltese voice / Dejjem vuċi bil-Malti"),
                ("en", "Always English voice / Dejjem vuċi bl-Ingliż"),
            )),
            ("comment_speed", "Reading speed / Veloċità tal-qari", (
                ("slow", "Slow / Bil-mod"),
                ("normal", "Normal / Normali"),
                ("fast", "Fast / Malajr"),
            )),
        ):
            combo = QComboBox()
            combo.setMinimumHeight(40)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            for value, caption in options:
                combo.addItem(caption, value)
            index = combo.findData(self.settings[setting])
            combo.setCurrentIndex(index if index >= 0 else combo.findData(DEFAULTS[setting]))
            combo.currentIndexChanged.connect(
                lambda index, field=combo, name=setting: self.change(name, field.itemData(index)))
            caption = QLabel(label)
            caption.setBuddy(combo)
            combo.setAccessibleName(label)
            page.addWidget(caption)
            page.addWidget(combo)
        for setting, label in (
            ("comment_author", "Say who wrote it / Għid min kitbu"),
            ("comment_skip_greetings", "Skip hello, hi and emoji-only comments\nAqbeż kummenti b’hello, hi jew emojis biss"),
            ("comment_mentions_only", "Only read comments that mention my name\nAqra biss kummenti li jsemmu ismi"),
            ("learn_read_words", "Learn words while reading / Tgħallem kliem waqt il-qari"),
        ):
            check = QCheckBox(label)
            check.setMinimumHeight(40)
            check.setChecked(self.settings[setting])
            check.toggled.connect(lambda enabled, name=setting: self.change(name, enabled))
            page.addWidget(check)
        forget = QPushButton("Forget learned words")
        forget.clicked.connect(lambda: self.screen_reader.forget_words())
        page.addWidget(forget)
        self.listen_checkbox = QCheckBox("👂 Listen and learn / Isma' u tgħallem")
        self.listen_checkbox.toggled.connect(self.toggle_listen)
        page.addWidget(self.listen_checkbox)
        page.addWidget(QLabel("Last 20 texts"))
        self.items = QListWidget()
        self.items.setAccessibleName("History")
        page.addWidget(self.items, 1)
        history_actions = QHBoxLayout()
        for label, callback in (("🔊 Read text", self.read_history), ("Fix", self.fix_history),
                                ("× Clear history", self.clear_history)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            history_actions.addWidget(button)
        page.addLayout(history_actions)
        area = self.application.primaryScreen().availableGeometry()
        self.resize(min(730, area.width()), min(815, area.height()))

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("Il-Kelma · Speak & Write")
        self.menu = QMenu(self)
        self.menu.addAction("Settings", self.open_window)
        self.menu.addAction("Show Kelma Buddy", self.buddy.show)
        self.pad_action = self.menu.addAction("📝 Kelma Pad / Il-Karta", self.open_pad)
        self.menu.addAction("Quit Il-Kelma", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(
            lambda reason: self.open_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def engine_changed(self):
        self.engine.backend = self.backend.currentData()
        self.change("engine", self.engine.backend)

    def open_window(self):
        if self.state in ("busy", "reading", "listening"):
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def save_name(self, field, key):
        value = field.text().strip() or DEFAULTS[key]
        field.setText(value)
        if value != self.settings[key]:
            self.change(key, value)
            self.buddy.name = self.settings["buddy_name"]
            self.bubble("greeting", user=self.settings["user_name"], buddy=self.settings["buddy_name"])

    def save_buddy_position(self, point):
        self.change("buddy_position", [point.x(), point.y()])

    def toggle_clip(self):
        target = self.buddy_target()
        if self.settings.get("clip"):
            self.clipped = self.clip_rect = None
            self.change("clip", None)
            self.buddy.clipped = False
            self.save_buddy_position(self.buddy.anchor)
            self.bubble("unclipped")
            return
        if target and (point := self.desktop.focused_box()):
            box = replace(target, point=point)
            if self.desktop.window_at(point) == target.hwnd and self.desktop.box_bounds(box):
                if saved := self.desktop.save_clip(box):
                    self.change("clip", saved)
                    self.bind_clip(box)
                    self.bubble("clipped")
                    return
        self.bubble("typing_box")

    def bind_clip(self, target):
        self.clipped = target
        self.clip_rect = win32gui.GetWindowRect(target.hwnd)
        self.buddy.clipped = True
        self.place_clipped_buddy()
        self.buddy.refresh()

    def track_clip(self, startup=False):
        saved = self.settings.get("clip")
        if not saved:
            return
        if self.clipped and self.clipped.valid(self.desktop):
            try:
                rect = win32gui.GetWindowRect(self.clipped.hwnd)
                if win32gui.IsIconic(self.clipped.hwnd):
                    return
            except win32gui.error:
                self.clipped = None
            else:
                if rect != self.clip_rect:
                    left, top, _, _ = rect
                    dx, dy = saved["point"]
                    self.clipped = replace(self.clipped, point=(left + dx, top + dy))
                    self.clip_rect = rect
                    self.place_clipped_buddy()
                return
        self.clipped = None
        if target := self.desktop.restore_clip(saved):
            self.bind_clip(target)
        elif startup:
            self.change("clip", None)
        elif not self.buddy.clipped:
            self.buddy.clipped = True
            self.buddy.refresh()

    def place_clipped_buddy(self):
        origins = screen_origins()
        x, y = self.clipped.point
        for screen in self.application.screens():
            ratio = screen.devicePixelRatio()
            geometry = screen.geometry()
            left, top = origins.get(screen.name(), (geometry.x(), geometry.y()))
            if left <= x < left + geometry.width() * ratio and top <= y < top + geometry.height() * ratio:
                break
        else:
            return

        def logical(px, py):
            return geometry.topLeft() + QPoint(round((px - left) / ratio), round((py - top) / ratio))

        bounds = self.desktop.box_bounds(self.clipped)
        box = QRect(logical(*bounds[:2]), logical(*bounds[2:])) if bounds else QRect(logical(x, y), logical(x, y))
        self.buddy.set_position(buddy_position(box, screen.availableGeometry(), SIZE, SIZE + 18))

    def fill_language_box(self):
        self.language.blockSignals(True)
        self.language.clear()
        self.language.addItem("Auto", "")
        for code, pack in chosen().items():
            self.language.addItem(pack["name"], code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.settings["language"])))
        self.language.blockSignals(False)

    def my_languages_changed(self, _item=None):
        codes = [self.my_languages.item(row).data(Qt.ItemDataRole.UserRole)
                 for row in range(self.my_languages.count())
                 if self.my_languages.item(row).checkState() == Qt.CheckState.Checked]
        set_chosen(codes)
        self.change("my_languages", list(chosen()))
        if self.settings["language"] not in chosen():
            self.change("language", "")
            self.buddy.language = ""
        self.fill_language_box()
        self.buddy.load_page()
        if self.pad is not None:
            self.pad.fill_languages()

    def language_changed(self):
        language = self.language.currentData()
        self.change("language", language)
        self.buddy.language = language
        self.buddy.refresh()

    def cycle_buddy_language(self):
        code = next_language(self.settings["language"])
        self.language.setCurrentIndex(self.language.findData(code))
        self.bubble("language")

    def buddy_target(self):
        target = self.foreground_tracker.current()
        if target and target.focus(self.desktop):
            return target
        return None

    def after_chat_selection(self, action):
        self.screen_reader.cancel_selection()
        action()

    def buddy_action(self, action):
        if action == "pad":
            self.open_pad()
            return
        if action == "listen-learn":
            self.listen_checkbox.setChecked(not self.listen_checkbox.isChecked())
            return
        screen_actions = {"point": self.screen_reader.point, "comments": self.screen_reader.toggle_comments,
                          "stop-comments": self.screen_reader.stop_comments,
                          "draw-comments": self.screen_reader.draw_comments}
        if action in screen_actions:
            screen_actions[action]()
            return
        if action == "clip":
            self.toggle_clip()
            return

        if self.recorder and self.capture_mode == "pad" and action in ("write", "help"):
            self.finish_recording()
            return
        target = self.buddy_target()
        if action == "write" and self.recorder:
            self.toggle()
        elif action == "help" and (self.recorder or self.pending_phrase):
            self.help_with_phrase()
        elif action == "read":
            if target:
                self.read_selection(target)
            else:
                self.bubble("select_text")
        elif action == "last":
            if target:
                point = self.desktop.focused_box()
                if self.clipped and self.clipped.hwnd == target.hwnd:
                    point = self.clipped.point
                self.read_last(replace(target, point=point))
            else:
                self.bubble("no_message")
        elif action in ("write", "help"):
            if target:
                clip = self.clipped
                if clip and clip.hwnd == target.hwnd and clip.valid(self.desktop):
                    point = clip.point
                else:
                    point = self.desktop.focused_box()
                if (point and self.desktop.window_at(point) == target.hwnd
                        and self.desktop.box_bounds(replace(target, point=point))):
                    target = replace(target, focus_point=point)
                    if target.focus(self.desktop):
                        callback = self.toggle if action == "write" else self.help_with_phrase
                        callback(target)
                        return
            self.bubble("typing_box")
        else:
            callbacks = {"lang": self.cycle_buddy_language, "settings": self.open_window,
                         "hide": self.buddy.hide, "quit": self.quit}
            callbacks[action]()

    def build_phrase_preview(self):
        self.phrase_panel = QWidget(None, TOOL | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.phrase_panel.setObjectName("settingsPage")
        self.phrase_panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout = QVBoxLayout(self.phrase_panel)
        self.phrase_text = QLabel()
        self.phrase_text.setTextFormat(Qt.TextFormat.PlainText)
        self.phrase_text.setWordWrap(True)
        self.phrase_text.setStyleSheet("font-size: 24pt; font-weight: bold;")
        layout.addWidget(self.phrase_text)
        self.phrase_source = QLabel()
        layout.addWidget(self.phrase_source)
        self.phrase_choices = []
        for index in range(3):
            row = QGridLayout()
            choice = QPushButton()
            choice.clicked.connect(lambda checked=False, i=index: self.pick_phrase(i))
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            row.addWidget(choice, 0, 0)
            row.addWidget(label, 0, 1)
            row.setColumnStretch(1, 1)
            layout.addLayout(row)
            self.phrase_choices.append((choice, label))
        self.phrase_hint = QLabel("Put it in, or use the mic and say “iva”.")
        self.phrase_hint.setWordWrap(True)
        layout.addWidget(self.phrase_hint)
        buttons = QGridLayout()
        self.put_button = QPushButton("✅ Put it in")
        self.put_button.clicked.connect(self.accept_phrase)
        buttons.addWidget(self.put_button, 0, 0)
        self.voice_button = QPushButton("🎤 Say iva")
        self.voice_button.clicked.connect(self.record_confirmation)
        buttons.addWidget(self.voice_button, 0, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel)
        buttons.addWidget(cancel, 1, 0, 1, 2)
        layout.addLayout(buttons)

    def help_with_phrase(self, target=None):
        if self.recorder:
            if self.capture_mode in ("phrase", "confirm"):
                self.finish_recording()
        elif self.pending_phrase:
            self.record_confirmation()
        else:
            self.start_recording("phrase", target, self.settings["language"] or "mt")

    def prepare_phrase(self, result):
        idea = result["text"]
        if not phrase_key(idea):
            self.ask("nothing_heard")
            return
        language = self.capture_language
        target = self.capture_target
        self.show_status("busy", "Finding the words")

        def prepare():
            spoken = corrected(idea, result["language"])
            try:
                return suggest_options(idea, language, DEFAULT_MODEL, corrected_idea=spoken)
            except (OSError, ValueError, KeyError):
                logging.exception("Help me say it could not get a sentence")
                return None

        def preview(options):
            if options:
                self.preview_phrase({"text": options[0].text, "idea": idea, "language": language,
                                     "options": options, "selected": 0}, target)
            else:
                self.ask("phrase_failed")

        self.run(prepare, preview)

    def preview_phrase(self, result, target):
        self.pending_phrase = (result, target)
        self.phrase_text.setText(result["text"])
        self.update_phrase_options(result)
        area = self.buddy.screen().availableGeometry()
        self.phrase_panel.setFixedWidth(min(610, area.width() - 20))
        self.phrase_panel.adjustSize()
        self.phrase_panel.move(area.center() - self.phrase_panel.rect().center())
        self.phrase_panel.show()
        self.show_status("idle", "Ready to check")
        if mood := reaction(result["idea"]):
            self.bubble(mood, user=self.settings["user_name"])
        else:
            self.bubble("phrase_ready")
        self.hotkeys.bind(3, "Esc")
        self.voice_button.setEnabled(False)
        self.voice_button.setText("🎤 Use mic" if len(result.get("options", [])) > 1 else "🎤 Say iva")
        self.phrase_audio.set()
        stop = self.phrase_audio = Event()

        def spoken(online_failed):
            if self.pending_phrase and self.phrase_audio is stop:
                self.voice_button.setEnabled(True)
                if online_failed:
                    self.bubble("voices_failed", speak_message=False)

        self.run(lambda: self.read_phrase(result, stop), spoken)

    def update_phrase_options(self, result):
        options = result.get("options", [])
        selected = result.get("selected", 0)
        self.phrase_source.setText(options[selected].source if options else "")
        for index, (button, label) in enumerate(self.phrase_choices):
            visible = len(options) > 1 and index < len(options)
            button.setVisible(visible)
            label.setVisible(visible and index != selected)
            if visible:
                button.setText(f"{index + 1} · " + ("Read again" if index == selected else "Pick"))
                label.setText(options[index].text)
                label.setStyleSheet("font-size: 14pt; font-weight: bold;" if index == 0
                                    else "font-size: 12pt;")
        self.phrase_hint.setText("Say one, two or three to pick. Say “iva” or tap Put it in to use it."
                                 if len(options) > 1 else "Put it in, or use the mic and say “iva”.")

    def pick_phrase(self, index):
        if not self.pending_phrase or self.recorder:
            return
        result, target = self.pending_phrase
        options = result.get("options", [])
        if 0 <= index < len(options):
            self.preview_phrase({**result, "text": options[index].text, "selected": index}, target)

    def read_phrase(self, result, stop):
        if stop.is_set():
            return
        online = self.settings["voices"]
        if online:
            try:
                play(result["text"], result["language"], stop)
                return
            except Exception:
                logging.exception("Could not play the phrase in the selected voice")
        if not stop.is_set():
            try:
                announce(result["text"])
            except Exception:
                logging.exception("Could not play the phrase with the Windows voice")
        return online and not stop.is_set()

    def record_confirmation(self):
        if not self.pending_phrase or not self.voice_button.isEnabled():
            return
        if self.recorder:
            if self.capture_mode == "confirm":
                self.finish_recording()
            return
        self.phrase_audio.set()
        self.start_recording("confirm", language="mt")
        if self.recorder:
            self.voice_button.setText("Stop and check")

    def has_phrase_option(self, index):
        return bool(self.pending_phrase and 0 <= index < len(self.pending_phrase[0].get("options", [])))

    def voice_confirmation(self, result):
        if not self.pending_phrase:
            return
        if re.fullmatch(r"\s*iva[.!?]*\s*", result["text"], re.IGNORECASE):
            self.accept_phrase()
        elif (index := spoken_option(result["text"])) is not None and self.has_phrase_option(index):
            self.pick_phrase(index)
        else:
            if self.has_phrase_option(1):
                self.voice_button.setText("🎤 Use mic")
                self.phrase_hint.setText("Try saying “iva” or an option number, or tap Put it in.")
            else:
                self.voice_button.setText("🎤 Say iva")
                self.phrase_hint.setText("I didn’t hear “iva”. Try again or tap Put it in.")
            self.hotkeys.bind(3, "Esc")
            self.show_status("idle", "Ready to check")
            self.bubble("confirm_retry")

    def dismiss_phrase(self):
        self.pending_phrase = None
        self.phrase_audio.set()
        self.phrase_panel.hide()
        self.hotkeys.unbind(3)
        self.show_status("idle", "Ready")

    def accept_phrase(self):
        if not self.pending_phrase:
            return
        if self.recorder:
            self.recorder.close()
            self.recorder = None
        result, target = self.pending_phrase
        try:
            confirm_phrase(result["idea"], result["language"], result["text"])
        except OSError:
            logging.exception("Could not save the approved phrase")
            self.voice_button.setText("🎤 Say iva")
            self.show_status("idle", "Ready to check")
            self.bubble("phrase_save_failed")
            return
        self.dismiss_phrase()
        self.paste_text(result, target)
        if self.state == "idle":
            self.bubble("phrase_saved")

    def read_last(self, target):
        if self.state in ("busy", "reading", "listening") or self.pending_phrase:
            return
        if not self.settings["voices"]:
            self.ask("voices_off")
        else:
            self.show_status("reading", "Reading")
            self.run(lambda: self.desktop.last_message(target),
                     lambda text: self.read(text, empty="no_message"))

    def change(self, name, value):
        self.settings[name] = value
        save("settings", self.settings)
        if name == "dark":
            self.theme()

    def track_pad_target(self):
        target = self.foreground_tracker.current()
        if not target or not self.desktop.is_foreground(target.hwnd):
            return
        point = self.desktop.focused_box(editor_only=True)
        if point and self.desktop.window_at(point) == target.hwnd:
            self.pad_target = replace(target, focus_point=point)

    def open_pad(self):
        self.track_pad_target()
        if self.pad is None:
            self.pad = Pad(self)
        if self.pad.isMinimized():
            self.pad.showNormal()
        else:
            self.pad.show()
        self.pad.raise_()
        self.pad.activateWindow()

    def paste_pad(self, text):
        target = self.pad_target
        try:
            point = target.focus_point if target else None
            if (point and target.valid(self.desktop) and self.desktop.window_at(point) == target.hwnd
                    and target.focus(self.desktop)
                    and self.desktop.focused_box(editor_only=True) == point
                    and self.desktop.paste(target, text)):
                self.pad.mood("done", "Daħal fiċ-chat!", brief=True)
                return
        except Exception:
            logging.exception("Could not paste from Kelma Pad")
        self.pad.mood("oops", "Click in your chat box first. / Agħfas fil-kaxxa taċ-chat l-ewwel.")

    def pad_capture(self, mode, language, deliver):
        if self.pad_job:
            if self.recorder and self.capture_mode == "pad":
                self.finish_recording()
            return
        if self.recorder or self.pending_phrase or self.state in ("busy", "reading", "listening"):
            self.pad.mood("oops", "Buddy is busy. / Kelma qed taħdem.", brief=True)
            return
        if self.probes is not None or (not self.test_audio and self.active_mic not in self.devices):
            self.pad.mood("oops", "Microphone unavailable. / Il-mikrofonu mhux disponibbli.")
            return
        self.pad_job = (mode, language, deliver)
        self.start_recording("pad", language=language)

    def finish_pad_capture(self, recorder, job):
        mode, language, deliver = job
        self.pad.mood("think", "Qed naħseb…")

        def work():
            try:
                audio = recorder.wav()
                if mode == "help":
                    idea = self.engine.transcribe_idea(audio)
                    text = idea["text"]
                    if not phrase_key(text):
                        return None
                    spoken = corrected(text, idea["language"])
                    return text, suggest_options(text, language, DEFAULT_MODEL, corrected_idea=spoken)
                result = self.engine.transcribe(audio, language)
                text = result["text"]
                if language == "mt":
                    text = apply_corrections(text, WordStore().spelling())
                return (corrected(text, result["language"]),) if text else None
            except Exception:
                logging.exception("Kelma Pad dictation failed")
                return None

        def finished(result):
            if self.pad_job is not job:
                return
            self.pad_job = None
            self.show_status("idle", "Ready")
            if result and all(result):
                deliver(*result)
            else:
                self.pad.mood("oops", "Try saying it again. / Erġa’ għidha.")

        self.run(work, finished)

    def cancel_pad_capture(self):
        if self.pad_job:
            self.pad_job = None
            if self.capture_mode == "pad" and self.recorder:
                recorder, self.recorder = self.recorder, None
                recorder.close()
                self.hotkeys.unbind(3)
            self.show_status("idle", "Ready")
            self.pad.mood("rest", "Ikteb jew tkellem.")

    def register_shortcuts(self):
        notices = []
        for slot, setting, action, fallbacks in (
            (1, "write_key", "Write", WRITE_FALLBACKS),
            (2, "read_key", "Read", READ_FALLBACKS),
        ):
            preferred = self.settings[setting]
            active = bind_startup(self.hotkeys, slot, preferred, fallbacks)
            self.show_active_key(slot)
            if active != preferred:
                replacement = f"Use {active} for {action}." if active else f"Open settings to choose a {action} key."
                notices.append(f"{preferred} could not be registered. {replacement}")
        return " ".join(notices)

    def show_active_key(self, slot):
        combo = self.key_boxes[slot]
        combo.blockSignals(True)
        combo.setCurrentText(self.hotkeys.bound.get(slot, "Unavailable"))
        combo.blockSignals(False)

    def register_help_shortcut(self):
        preferred = self.settings["help_key"]
        active = bind_startup(self.hotkeys, 4, preferred, HELP_FALLBACKS)
        self.show_active_key(4)
        if active and active != preferred:
            return f"Use {active} for Help me say it."
        if not active:
            return "Use Buddy for Help me say it."
        return ""

    def key_changed(self, slot, name, key):
        if self.hotkeys.bind(slot, key):
            self.change(name, key)
            if self.state in ("idle", "check"):
                self.show_status("idle", "Shortcut saved.")
        else:
            self.show_active_key(slot)
            self.bubble("key_used", key_name=key)

    def mic_name(self):
        if self.test_audio:
            return "Test audio (WAV)"
        device = self.devices.get(self.recording_mic if self.recorder else self.active_mic)
        return device.description() if device else "No microphone connected"

    def update_mic_display(self):
        name = self.mic_name()
        self.tray.setToolTip(f"Il-Kelma · {name}")
        state = self.state
        if state == "idle" and self.pending_phrase:
            state = "preview"
        self.buddy.set_state(mood_for(state, bool(self.active_mic or self.test_audio)))

    def refresh_devices(self):
        had_devices = bool(self.devices)
        testing = self.probes is not None
        self.devices = microphones()
        if testing:
            self.close_probes()
        if self.active_mic not in self.devices:
            self.stop_preview()
        if self.recorder and self.recording_mic not in self.devices:
            was_preferred = self.recording_mic == self.preferred
            pad_recording = self.capture_mode == "pad"
            self.cancel()
            if pad_recording:
                self.pad.mood("oops", "Microphone disconnected. / Il-mikrofonu nqata’.")
            elif not was_preferred:
                self.ask("mic_disconnected")
        self.apply_devices()
        if self.devices and (testing or not had_devices) and not self.settings["mic"]:
            self.calibrate_mics()

    def apply_devices(self):
        if self.preferred not in self.devices:
            # Also when Windows gives the same microphone a new ID (another USB port): find it by name.
            same_name = legacy_microphone(self.devices, self.settings["mic"])
            twins = [i for i, d in self.devices.items() if legacy_microphone({i: d}, self.settings["mic"])]
            if same_name and len(twins) == 1:
                self.remember_mic(same_name)
        preferred = self.preferred in self.devices
        self.active_mic = self.preferred if preferred else fallback_microphone(self.devices, self.peaks)
        self.microphone.blockSignals(True)
        self.microphone.clear()
        for identifier, device in self.devices.items():
            self.microphone.addItem(device.description(), identifier)
        self.microphone.setCurrentIndex(self.microphone.findData(self.active_mic))
        self.microphone.blockSignals(False)
        missing = bool(self.preferred or self.settings["mic"]) and not preferred
        if missing:
            offer = (f"Available for your next recording: {self.devices[self.active_mic].description()}. "
                     "Choose another mic here if you prefer." if self.active_mic else "Connect a microphone to continue.")
            self.setup.setText(offer)
            if not self.preferred_missing:
                self.bubble("mic_disconnected")
        elif self.preferred_missing:
            self.setup.setText("Your chosen microphone is back.")
            if self.state in ("idle", "check"):
                self.show_status("idle", "Microphone reconnected.")
                self.bubble("mic_reconnected")
        elif preferred:
            self.setup.setText("Test your microphone and watch the level bar.")
        else:
            self.setup.setText("Click Test mic and speak to choose a microphone automatically.")
        self.preferred_missing = missing
        self.update_mic_display()

    def remember_mic(self, identifier):
        self.preferred = identifier
        self.settings["mic_id"] = identifier
        self.change("mic", self.devices[identifier].description())

    def mic_changed(self):
        identifier = self.microphone.currentData()
        if identifier:
            self.stop_preview()
            self.remember_mic(identifier)
            self.apply_devices()

    def test_mic(self):
        if self.test_audio:
            return
        if not self.preferred and not self.settings["mic"]:
            self.calibrate_mics()
        else:
            self.toggle_preview()

    def calibrate_mics(self):
        if self.probes is not None or self.recorder or self.quitting:
            return
        self.setup.setText("Speak now while Il-Kelma compares your microphones.")
        self.microphone.setEnabled(False)
        self.test_button.setEnabled(False)
        self.probes = {}
        for identifier, device in self.devices.items():
            try:
                self.probes[identifier] = Recorder(device, keep=False)
            except Exception:
                logging.exception("Could not test %s", device.description())
        self.mic_test_timer = QTimer(self, singleShot=True, interval=4000)
        self.mic_test_timer.timeout.connect(self.finish_mic_test)
        self.mic_test_timer.start()

    def close_probes(self):
        self.mic_test_timer.stop()
        self.mic_test_timer.deleteLater()
        for recorder in self.probes.values():
            recorder.close()
        self.probes = None
        self.microphone.setEnabled(True)
        self.test_button.setEnabled(True)

    def finish_mic_test(self):
        self.peaks.update({identifier: recorder.peak for identifier, recorder in self.probes.items()})
        winner = loudest_microphone({identifier: self.peaks[identifier] for identifier in self.probes})
        self.close_probes()
        if winner is not None:
            self.remember_mic(winner)
        self.apply_devices()
        if winner is None:
            self.setup.setText("No speech detected. Click Test mic and speak again, or choose a microphone.")
            self.bubble("nothing_heard")

    def stop_preview(self):
        if self.preview:
            self.preview.close()
            self.preview = None
        self.test_button.setText("🎤 Test mic")

    def toggle_preview(self):
        if self.preview:
            self.stop_preview()
        elif self.recorder is None:
            device = self.devices.get(self.active_mic)
            if device is None:
                self.ask("no_mic")
                return
            try:
                self.preview = Recorder(device, keep=False)
                self.test_button.setText("■ Stop test")
            except Exception:
                logging.exception("Microphone test failed")
                self.ask("mic_open")

    def update_meter(self):
        recorder = self.recorder or self.preview
        level = recorder.level if recorder else max(
            (probe.level for probe in (self.probes or {}).values()), default=0)
        value = min(100, int(level * 360))
        self.meter.setValue(value)
        if self.recorder and self.recorder.finished.is_set():
            self.toggle()
        elif self.recorder and self.recorder.source is not None:
            recorder = self.recorder
            if recorder.source.error() != QAudio.Error.NoError:
                if self.capture_mode == "pad":
                    self.cancel_pad_capture()
                    self.pad.mood("oops", "Microphone problem. / Problema bil-mikrofonu.")
                    return
                name = self.mic_name()
                self.cancel()
                self.ask("mic_read", name=name)
            elif self.capture_mode == "confirm":
                if recorder.peak >= SOUND_LEVEL and monotonic() - recorder.last_sound >= 0.9:
                    self.finish_recording()
            elif self.capture_mode != "pad" and not recorder.paused:
                if not recorder.silent():
                    self.silence_warned = False
                elif not self.silence_warned:
                    self.warn_silence(recorder)

    def warn_silence(self, recorder):
        self.silence_warned = True
        recorder.paused = True

        def resume():
            if self.recorder is recorder:
                recorder.paused = False

        self.bubble("silence", after=resume, offline=True)

    def listen_foreground(self):
        if not (self.recorder or self.pending_phrase or self.probes):
            return self.listen_slot
        return None

    def toggle_listen(self, enabled):
        self.buddy.listen_active = enabled
        if enabled:
            self.listen_timer.start()
            if self.listen_stop is None:
                stop = self.listen_stop = Event()
                names = " ".join(self.settings[key] for key in ("user_name", "buddy_name"))
                excluded = {word for _, word, *_ in tokens(names)}

                def learn():
                    try:
                        result = listen(self.engine, self.screen_reader.words, stop,
                                        self.listen_foreground, excluded)
                    except Exception:
                        result = "unavailable"
                    self.completion.listening_done.emit(result)

                Thread(target=learn, name="listen-and-learn", daemon=True).start()
            self.buddy.show_message("Qed nisma' u nitgħallem", "listen")
        else:
            self.listen_timer.stop()
            if self.listen_stop is not None:
                self.listen_stop.set()
            self.buddy.show_message("Waqaft nisma'", "rest")

    def listen_finished(self, result):
        self.listen_stop = None
        if self.quitting or not self.listen_checkbox.isChecked():
            return
        if result == "stopped":
            self.toggle_listen(True)
            return
        if result == "timeout":
            self.listen_timed_out()
            return
        self.listen_checkbox.setChecked(False)
        if result == "unavailable":
            self.buddy.show_message("Waqaft nisma'. Could not listen to playback.", "oops")

    def listen_timed_out(self):
        if self.listen_checkbox.isChecked():
            self.listen_checkbox.setChecked(False)
            self.buddy.show_message("Waqaft nisma' wara 60 minuta", "rest")

    def show_status(self, state, message):
        self.listen_slot = None if state in ("reading", "listening", "busy") else object()
        self.state = state
        if state in ("reading", "listening", "busy"):
            self.screen_reader.pause()
        elif self.screen_reader.paused:
            # Let the current paste or cancel handler finish before capturing again.
            QTimer.singleShot(0, self.screen_reader.resume)
        self.status.setText(message)
        self.update_mic_display()

    def ask(self, message, **values):
        self.show_status("check", feedback(self.settings["language"], message, **values)[0])
        self.bubble(message, **values)

    def bubble(self, message, *, after=None, hold=None, speak_message=True, offline=False, **values):
        language = self.settings["language"] or "mt"
        text, mood, speak = feedback(language, message, **values)
        language = buddy_message(language, message)[1]
        self.buddy.show_message(text, mood, hold)
        if speak and speak_message and self.settings["buddy_speaks"]:
            self.stop_bubble_audio.set()
            stop = self.stop_bubble_audio = Event()

            def speak_message():
                if stop.is_set():
                    return False
                if self.settings["voices"] and not offline and message != "voices_failed":
                    try:
                        play(text, language, stop)
                        return False
                    except Exception:
                        logging.exception("Could not speak the Buddy message in the selected voice")
                        if not stop.is_set():
                            announce(text)
                        return True
                announce(text)
                return False

            def spoken(failed):
                if failed and not stop.is_set() and self.buddy.message == text:
                    self.buddy.set_reaction("sad", 6000)
                if after:
                    after()

            self.run(speak_message, spoken)
        elif after:
            after()

    def toggle(self, target=None, *, language=None):
        if self.recorder:
            self.finish_recording()
        elif self.pending_phrase is None:
            self.start_recording("write", target, language)

    def finish_recording(self):
        if self.recorder:
            name = self.mic_name()
            recorder, self.recorder = self.recorder, None
            self.hotkeys.unbind(3)
            if recorder.test_wav is None:
                recorder.close()
                if not recorder.has_samples:
                    if self.capture_mode == "pad":
                        self.cancel_pad_capture()
                        self.pad.mood("oops", "I did not hear anything. / Ma smajt xejn.")
                        return
                    if self.capture_mode == "confirm":
                        self.voice_confirmation({"text": ""})
                        return
                    if self.capture_mode == "fix" and self.fix_dialog.isVisible():
                        self.fix_mic.setText("🎤 Say correction")
                    self.ask("no_sound", name=name)
                    return
            mode = self.capture_mode
            self.show_status("busy", "Writing" if mode == "write" else "Working")
            if mode == "pad":
                self.finish_pad_capture(recorder, self.pad_job)
                return
            language = self.capture_language or None

            def finish_recording():
                audio = recorder.wav()
                winsound.MessageBeep(winsound.MB_OK)
                if mode == "phrase" or (mode == "confirm" and pending
                                        and len(pending[0].get("options", [])) > 1):
                    return self.engine.transcribe_idea(audio)
                return self.engine.transcribe(audio, language)

            pending = self.pending_phrase
            if mode == "confirm":
                self.run(finish_recording, lambda result: self.voice_confirmation(result)
                         if self.pending_phrase is pending else None)
            else:
                callback = {"write": self.paste_result, "phrase": self.prepare_phrase,
                            "fix": self.spoken_fix}[mode]
                self.run(finish_recording, callback)

    def start_recording(self, mode, target=None, language=None):
        if self.state not in ("busy", "reading", "listening"):
            if self.probes is not None:
                self.ask("mic_test_wait")
                return
            device = self.devices.get(self.active_mic)
            if device is None and not self.test_audio:
                self.ask("no_mic")
                return
            if mode in ("write", "phrase"):
                target = target or self.desktop.remember()
                logging.info("Recording target: %s", self.desktop.describe(target.hwnd))
                if not target.valid(self.desktop) or target.pid == self.application.applicationPid():
                    self.ask("typing_box")
                    return
                self.capture_target = target
            self.stop_preview()
            self.stop_bubble_audio.set()
            self.buddy.dismiss()
            self.capture_mode = mode
            if mode in ("write", "phrase"):
                self.screen_reader.pause()
            self.capture_language = self.language.currentData() if language is None and mode == "write" else language
            self.listen_slot = None
            try:
                if not self.test_audio and mode != "confirm":
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                options = {"max_seconds": 7} if mode == "confirm" else {}
                self.recorder = Recorder(device, test_audio=self.test_audio, **options)
                self.recording_mic = self.active_mic
                self.silence_warned = False
                self.hotkeys.bind(3, "Esc")
                message = {
                    "pad": "Listening in Kelma Pad",
                    "phrase": "Speak your idea. Press Help me say it again when done.",
                    "confirm": ("Say an option number or “iva”."
                                if self.pending_phrase and len(self.pending_phrase[0].get("options", [])) > 1
                                else "Say “iva”."),
                    "fix": "Say the correction, then stop and check.",
                    "write": "Listening",
                }
                self.show_status("listening", message[mode])
                if mode == "pad":
                    self.pad.mood("listen", "Qed nisma’… Agħfas il-buttuna biex tieqaf.")
                elif mode == "phrase":
                    self.bubble("say_idea")
                elif mode == "confirm":
                    self.bubble("say_option" if self.has_phrase_option(1) else "say_iva")
            except Exception:
                logging.exception("Recording failed")
                if mode == "pad":
                    self.cancel_pad_capture()
                    self.pad.mood("oops", "Could not record. / Ma stajtx nirrekordja.")
                else:
                    self.ask("wav_failed" if self.test_audio else "mic_open")

    def cancel(self):
        if self.pad_job:
            self.cancel_pad_capture()
            return
        if self.recorder:
            self.recorder.close()
            self.recorder = None
            self.hotkeys.unbind(3)
            self.show_status("idle", "Recording cancelled.")
            if self.capture_mode == "fix" and self.fix_dialog:
                self.fix_mic.setText("🎤 Say correction")
        if self.pending_phrase:
            self.dismiss_phrase()

    def paste_result(self, result):
        text, language = result["text"], result["language"]
        if not text:
            self.ask("nothing_heard")
            return
        result = {**result, "text": corrected(text, language)}
        self.paste_text(result, self.capture_target, read_back=True)

    def paste_text(self, result, target, *, read_back=False):
        text, language = result["text"], result["language"]
        self.history = ([result] + self.history)[:20]
        save("history", self.history)
        self.refresh_history()
        try:
            pasted = self.desktop.paste(target, text)
        except Exception:
            logging.exception("Could not paste")
            self.ask("paste_history")
            return
        if not pasted:
            self.ask("paste_clipboard")
        else:
            self.show_status("idle", "Paste sent.")
            if read_back and self.settings["read_back"] and self.settings["voices"]:
                self.read(text, language, pasted=True)
            else:
                self.show_paste_feedback(text)

    def show_paste_feedback(self, text):
        if mood := reaction(text):
            self.bubble(mood, user=self.settings["user_name"])
        else:
            self.bubble("pasted", hold=4000)

    def read_selection(self, clipped=None):
        if self.state in ("busy", "reading", "listening") or self.pending_phrase:
            return
        if not self.settings["voices"]:
            self.ask("voices_off")
            return
        target = clipped or self.desktop.remember()
        self.show_status("reading", "Reading")

        def selected_text():
            text = self.desktop.selection(target)
            if not text and target.point:
                return self.desktop.point_text(target)
            return text

        self.run(selected_text, self.read)

    def read(self, text, language=None, empty="select_text", *, pasted=False):
        if not text:
            self.ask(empty)
        elif not self.settings["voices"]:
            self.ask("voices_off")
        else:
            self.stop_bubble_audio.set()
            self.show_status("reading", "Reading")

            def read_text():
                try:
                    play(text, language or detect(text), self.stop_audio)
                    return True
                except Exception:
                    logging.exception("Could not read aloud")
                    return False

            def finished(ok):
                if ok:
                    self.show_status("idle", "Ready")
                    if pasted:
                        self.show_paste_feedback(text)
                else:
                    self.ask("voices_failed")

            self.run(read_text, finished)

    def read_screen(self):
        if self.state not in ("busy", "reading", "listening"):
            text = (f"{GUIDE} {self.status.text()} {self.setup.text()} "
                    f"Microphone: {self.microphone.currentText()}. Language: {self.language.currentText()}. "
                    f"Write key {self.key_boxes[1].currentText()}. Read key {self.key_boxes[2].currentText()}. "
                    f"Help me say it key {self.key_boxes[4].currentText()}. "
                    "Read back after pasting. Use online voices. Soft dark theme. Last 20 texts.")
            self.read(text, "en")

    def refresh_history(self):
        self.items.clear()
        for entry in self.history:
            self.items.addItem(entry["text"])

    def read_history(self):
        index = self.items.currentRow()
        if index >= 0 and self.state not in ("busy", "reading", "listening") and not self.pending_phrase:
            entry = self.history[index]
            self.read(entry["text"], entry["language"])

    def fix_history(self):
        index = self.items.currentRow()
        if index < 0 or self.state in ("busy", "reading", "listening") or self.pending_phrase:
            return
        entry = self.history[index]
        dialog = QDialog(self)
        self.fix_dialog = dialog
        dialog.setObjectName("settingsPage")
        dialog.setWindowTitle("Fix what I heard")
        layout = QFormLayout(dialog)
        original = QLabel(entry["text"])
        original.setTextFormat(Qt.TextFormat.PlainText)
        original.setWordWrap(True)
        original.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addRow(original)
        heard = QLineEdit()
        self.fix_correct = QLineEdit()
        name = QCheckBox("This is a name")
        layout.addRow("Word or phrase I heard", heard)
        layout.addRow("Correct word or phrase", self.fix_correct)
        layout.addRow(name)
        self.fix_mic = QPushButton("🎤 Say correction")
        self.fix_mic.clicked.connect(lambda: self.record_fix(entry["language"]))
        layout.addRow(self.fix_mic)
        buttons = QHBoxLayout()
        save_button = QPushButton("Save fix")
        cancel_button = QPushButton("Cancel")
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addRow(buttons)

        def save_fix():
            if self.state in ("busy", "reading", "listening"):
                return
            try:
                mapping = learn_word(entry["language"], heard.text(), self.fix_correct.text(),
                                     name=name.isChecked())
                entry["text"] = apply_corrections(entry["text"], mapping)
                if entry.get("idea"):
                    confirm_phrase(entry["idea"], entry["language"], entry["text"])
                save("history", self.history)
            except ValueError:
                self.bubble("fix_input")
                return
            except OSError:
                logging.exception("Could not save the correction")
                self.bubble("fix_failed")
                return
            self.refresh_history()
            self.items.setCurrentRow(index)
            dialog.accept()
            self.bubble("word_saved")

        save_button.clicked.connect(save_fix)
        cancel_button.clicked.connect(dialog.reject)
        dialog.resize(560, dialog.sizeHint().height())
        dialog.exec()
        if self.recorder and self.capture_mode == "fix":
            self.cancel()
        dialog.deleteLater()
        self.fix_dialog = None

    def record_fix(self, language):
        if self.recorder and self.capture_mode == "fix":
            self.finish_recording()
        elif not self.recorder:
            self.start_recording("fix", language=language)
            if self.recorder:
                self.fix_mic.setText("Stop and check")

    def spoken_fix(self, result):
        if self.fix_dialog:
            self.fix_mic.setText("🎤 Say correction")
            self.fix_correct.setText(result["text"].strip().rstrip(".!?"))
        self.show_status("idle", "Check the correction")

    def clear_history(self):
        self.history.clear()
        save("history", self.history)
        self.refresh_history()

    def run(self, operation, success):
        future = self.executor.submit(operation)
        future.add_done_callback(lambda done: self.completion.done.emit(done, success))

    def completed(self, future, success):
        if self.quitting:
            return
        try:
            success(future.result())
        except Exception:
            logging.exception("Task failed")
            self.ask("task_failed")

    def theme(self):
        dark = self.settings["dark"]
        background, foreground = ("#23292d", "#f4ecdc") if dark else ("#f7efdf", "#262b30")
        surface, border = ("#333d45", "#72808a") if dark else ("#fff9ee", "#988b78")
        self.application.setStyleSheet(f"""
            QMainWindow, QMenu, QWidget#settingsPage, QScrollArea
                {{background:{background}; color:{foreground}; border:0;}}
            QLabel, QCheckBox {{color:{foreground};}}
            QLabel#title {{font-size:30pt; font-weight:bold;}}
            QLabel#settingsSection {{font-size:16pt; font-weight:bold;}}
            QPushButton, QComboBox, QListWidget {{background:{surface}; color:{foreground};
                border:1px solid {border}; border-radius:9px; padding:7px 12px;}}
            QComboBox QAbstractItemView {{background:{surface}; color:{foreground};}}
            QPushButton:hover, QComboBox:focus, QPushButton:focus {{border:2px solid #4c83d5; padding:6px 11px;}}
            QProgressBar {{background:{surface}; border:1px solid {border}; border-radius:7px; min-height:13px;}}
            QProgressBar::chunk {{background:#26845d;border-radius:6px;}}
            QScrollBar:vertical {{background:{background}; width:12px;}}
            QScrollBar::handle:vertical {{background:{border}; min-height:30px; border-radius:5px;}}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
        """)

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        else:
            self.stop_preview()
            self.hide()
            event.ignore()

    def quit(self):
        if self.pad and self.pad.isVisible() and not self.pad.close():
            return
        self.quitting = True
        self.listen_timer.stop()
        if self.listen_stop is not None:
            self.listen_stop.set()
        self.screen_reader.stop_comments(message=None)
        self.screen_reader.pause()
        self.stop_audio.set()
        self.stop_bubble_audio.set()
        self.cancel()
        self.stop_preview()
        if self.probes is not None:
            self.close_probes()
        self.hotkeys.close()
        self.foreground_tracker.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.api:
            self.api.shutdown()
            self.api.server_close()
        self.application.quit()


def main():
    logging.basicConfig(filename=ROOT / "out" / "ilkelma.log", encoding="utf-8", errors="backslashreplace",
                        level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Il-Kelma")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    lock = QLockFile(str(ROOT / "data" / "ilkelma.lock"))
    if not lock.tryLock(0):
        logging.info("Il-Kelma is already open.")
        return 0
    capture_exclusion = CaptureExclusion()
    app.installEventFilter(capture_exclusion)
    font_id = QFontDatabase.addApplicationFont(str(ROOT / "assets" / "AtkinsonHyperlegible-Regular.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    app.setFont(QFont(families[0] if families else "Segoe UI", 13))
    first_run = load("settings", None) is None
    window = Window(app)
    if first_run:
        save("settings", window.settings)
        window.show()
    return app.exec()


if __name__ == "__main__":
    (ROOT / "out").mkdir(exist_ok=True)
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("Could not start Il-Kelma")
        raise
