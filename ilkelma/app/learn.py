"""Tgħallem il-Malti: idioms, proverbs and grammar notes inside the Kelma Pad.

Entries are drafts until a Maltese speaker presses "Tajba" on them. Which entries are
checked is saved in the user's data folder, never in the shipped file."""
import json
from pathlib import Path
import random
import re
import unicodedata

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import storage

BOOK = Path(__file__).resolve().parents[1] / "learn" / "maltese.json"


def plain(text):
    """Lower case without dots and bars, so 'Ħ' and 'h' both match when a voice drops them."""
    text = unicodedata.normalize("NFD", text.casefold().replace("ħ", "h"))
    return "".join(c for c in text if not unicodedata.combining(c))


def words(text):
    return set(re.findall(r"\w+", plain(text)))


def load_book(path=BOOK):
    return json.loads(path.read_text(encoding="utf-8"))["categories"]


def entry_id(category, index):
    return f"{category['id']}-{index}"


def find(categories, heard):
    """The entry the user asked about, or None. Each matching word scores one point."""
    heard_words = words(heard)
    best, best_score = None, 0
    for category in categories:
        for index, entry in enumerate(category["entries"]):
            own = {word for word in words(entry["text"]) if len(word) > 2}
            own |= {plain(key) for key in entry.get("keys", [])}
            score = len(own & heard_words)
            if score > best_score:
                best, best_score = (category, index), score
    return best


PERSONS = {"jiena": ("I", 0), "inti": ("you", 0), "huwa": ("he", 1), "hija": ("she", 1),
           "aħna": ("we", 0), "intom": ("you all", 0), "huma": ("they", 0)}


def english(person, tense, negative, forms):
    """'she does not take', 'we took', 'Take! (to one person)'."""
    base, third, past = forms
    if tense == 2:
        who = "to one person" if person == "inti" else "to more people"
        return f"{'Do not ' + base if negative else base.capitalize()}! ({who})"
    subject, singular = PERSONS[person]
    if tense == 0:
        if negative:
            return f"{subject} {'does' if singular else 'do'} not {base}"
        return f"{subject} {third if singular else base}"
    return f"{subject} did not {base}" if negative else f"{subject} {past}"


def make_questions(categories, rng=random):
    """Verb questions to type, and idiom/proverb questions to choose."""
    questions = []
    for category in categories:
        for entry in category["entries"]:
            forms = entry.get("en_forms")
            for tense, table in enumerate(entry.get("tables", [])):
                if not forms:
                    break
                for person, yes, no in table["rows"]:
                    for negative, answer in ((False, yes), (True, no)):
                        questions.append({"kind": "type", "answer": answer,
                                          "prompt": english(person, tense, negative, forms),
                                          "hint": table["title"]})
            if category["id"] in ("idjomi", "qwiel"):
                others = [e["text"] for c in categories if c["id"] in ("idjomi", "qwiel")
                          for e in c["entries"] if e is not entry]
                choices = rng.sample(others, min(2, len(others))) + [entry["text"]]
                rng.shuffle(choices)
                questions.append({"kind": "choose", "answer": entry["text"], "choices": choices,
                                  "prompt": entry["meaning_en"], "hint": category["en"]})
    rng.shuffle(questions)
    return questions


def check(answer, given):
    """'right', 'letters' (right word, missing ċ ġ ħ ż) or 'wrong'."""
    tidy = lambda text: " ".join(re.findall(r"[^\W\d_]+", text.casefold()))
    if tidy(given) == tidy(answer):
        return "right"
    if tidy(plain(given)) == tidy(plain(answer)):
        return "letters"
    return "wrong"


class Checked:
    def __init__(self):
        self.ids = set(storage.load("learn_checked", []))

    def __contains__(self, item):
        return item in self.ids

    def add(self, item):
        self.ids.add(item)
        storage.save("learn_checked", sorted(self.ids))


class LearnView(QWidget):
    """The Learn page shown in place of the writing area."""
    changed = Signal()
    mode = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("learnView")
        self.categories = load_book()
        self.checked = Checked()
        self.category, self.index = self.categories[0], 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 8, 22, 8)
        layout.setSpacing(14)
        self.category_row = QHBoxLayout()
        self.category_row.setSpacing(8)
        layout.addLayout(self.category_row)
        self.entry_row = QGridLayout()
        self.entry_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.entry_row.setSpacing(8)
        layout.addLayout(self.entry_row)
        self.status = QLabel()
        self.status.setObjectName("learnStatus")
        layout.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignLeft)
        self.title = QLabel()
        self.title.setObjectName("learnTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.title)
        self.details = QLabel()
        self.details.setObjectName("learnDetails")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setOpenExternalLinks(True)
        layout.addWidget(self.details)
        self.answer = QLineEdit()
        self.answer.setObjectName("learnAnswer")
        self.answer.setPlaceholderText("Ikteb bil-Malti… · Type it in Maltese…")
        self.answer.returnPressed.connect(self.check_answer)
        self.answer.hide()
        layout.addWidget(self.answer)
        layout.addStretch()
        self.quiz = None
        self.highlight = None
        self.show_entry(self.category, 0)

    @property
    def entry(self):
        return self.category["entries"][self.index]

    @property
    def speak_text(self):
        """What Read says: the entry, or the answer once a quiz question is done."""
        if self.quiz:
            return self.quiz["question"]["answer"] if self.quiz["done"] else ""
        return self.entry["text"]

    def start_quiz(self):
        self.quiz = {"questions": make_questions(self.categories), "at": 0, "score": 0, "done": False}
        self.show_question()

    def stop_quiz(self):
        self.quiz = None
        self.answer.hide()
        self.show_entry(self.category, self.index)

    def show_question(self):
        quiz = self.quiz
        quiz["question"], quiz["done"] = quiz["questions"][quiz["at"] % len(quiz["questions"])], False
        quiz["feedback"] = None
        question = quiz["question"]
        self.highlight = None
        self.clear(self.entry_row)
        if question["kind"] == "choose":
            for number, choice in enumerate(question["choices"]):
                self.entry_row.addWidget(self.chip(choice, False, lambda _=False, c=choice: self.check_answer(c)),
                                         0, number)
        self.answer.setVisible(question["kind"] == "type")
        self.answer.clear()
        self.status.setText(f"🎮 Logħba · Quiz   ⭐ {quiz['score']}   · {question['hint']}")
        self.status.setProperty("ok", False)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.title.setText(escape(question["prompt"]))
        self.details.setText("Kif tgħidha bil-Malti? · How do you say it in Maltese?"
                             if question["kind"] == "type" else "Liema waħda tfisser hekk? · Which one means this?")
        if question["kind"] == "type":
            self.answer.setFocus()
        self.changed.emit()

    def check_answer(self, given=None):
        quiz = self.quiz
        if not quiz or quiz["done"]:
            if quiz:
                quiz["at"] += 1
                self.show_question()
            return None
        given = self.answer.text() if given is None else given
        if not given.strip():
            return None
        answer = quiz["question"]["answer"]
        result = check(answer, given)
        quiz["done"] = True
        if result == "right":
            quiz["score"] += 1
            message = f"✔ Tajjeb ħafna! · Well done!  <b>{escape(answer)}</b>"
        elif result == "letters":
            message = (f"✨ Kważi! Il-kelma t-tajba: <b>{escape(answer)}</b> · Almost! Mind the letters "
                       "ċ ġ ħ ż għ")
        else:
            message = f"✖ Mhux din. It-tweġiba: <b>{escape(answer)}</b> · The answer is above."
        quiz["feedback"] = (message + "<br><small>Agħfas Enter jew Tajba għal oħra · "
                            "Press Enter or Tajba for the next one</small>")
        self.details.setText(quiz["feedback"])
        self.changed.emit()
        return result

    @property
    def current_id(self):
        return entry_id(self.category, self.index)

    @staticmethod
    def clear(row):
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def chip(self, text, selected, run):
        button = QPushButton(text)
        button.setObjectName("learnChipOn" if selected else "learnChip")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(run)
        return button

    def show_entry(self, category, index):
        self.quiz = None
        self.answer.hide()
        self.category, self.index, self.highlight = category, index, None
        self.clear(self.category_row)
        for item in self.categories:
            self.category_row.addWidget(self.chip(f"{item['icon']} {item['mt']}", item is category,
                                                  lambda _=False, c=item: self.show_entry(c, 0)))
        self.category_row.addWidget(self.chip("🎮 Logħba · Quiz", False, lambda: self.start_quiz()))
        self.category_row.addWidget(self.chip("💬 Nitkellmu · Talk", False, lambda: self.mode.emit("talk")))
        self.category_row.addStretch()
        self.clear(self.entry_row)
        for number, entry in enumerate(category["entries"]):
            mark = "✔" if entry_id(category, number) in self.checked else "⏳"
            self.entry_row.addWidget(self.chip(f"{entry['text'].split(',')[0]}  {mark}", number == index,
                                               lambda _=False, n=number: self.show_entry(self.category, n)),
                                     number // 5, number % 5)
        self.render()
        self.changed.emit()

    def render(self):
        if self.quiz:
            answer = self.quiz["question"]["answer"]
            if self.highlight:
                start, end = self.highlight
                self.details.setText(escape(answer[:start]) + '<span style="background:#FFE066;color:#14213D">'
                                     + escape(answer[start:end]) + "</span>" + escape(answer[end:]))
            elif self.quiz.get("feedback"):
                self.details.setText(self.quiz["feedback"])
            return
        ok = self.current_id in self.checked
        self.status.setText("✔ Ikkontrollata · checked" if ok else "⏳ Mhux ikkontrollata · not checked yet")
        self.status.setProperty("ok", ok)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        text = self.entry["text"]
        if self.highlight:
            start, end = self.highlight
            text = (escape(text[:start]) + '<span style="background:#FFE066;color:#14213D;border-radius:8px">'
                    + escape(text[start:end]) + "</span>" + escape(text[end:]))
        else:
            text = escape(text)
        self.title.setText(text)
        entry = self.entry
        self.details.setText(
            f"<p><b>Tifsira:</b> {escape(entry['meaning_mt'])}</p>"
            f"<p style='opacity:.8'><b>Meaning:</b> {escape(entry['meaning_en'])}</p>"
            f"<p><b>Eżempju:</b> <i>{escape(entry['example'])}</i></p>" + tables_html(entry.get("tables", []))
            + (f"<p>⏳ <b>Iċċekkja · check:</b> {escape(', '.join(entry['to_check']))}</p>" if entry.get("to_check") else "")
            + (f"<p><a href='{entry['link']}'>Aktar fuq verb.mt · More on verb.mt</a></p>" if entry.get("link") else ""))

    def mark_word(self, start, end):
        self.highlight = (start, end) if end > start else None
        self.render()

    def approve(self):
        self.checked.add(self.current_id)
        self.show_entry(self.category, self.index)

    def go_to(self, heard):
        found = find(self.categories, heard)
        if found:
            self.show_entry(*found)
        return found is not None


def escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tables_html(tables):
    """Verb tables side by side, in big clear cells."""
    cells = []
    for table in tables:
        head = "".join(f"<th align='left' style='padding:4px 14px'>{escape(h)}</th>" for h in table["head"])
        rows = "".join("<tr>" + "".join(f"<td style='padding:3px 14px'>{escape(c)}</td>" for c in row) + "</tr>"
                       for row in table["rows"])
        cells.append(f"<td valign='top' style='padding-right:26px'><b>{escape(table['title'])}</b>"
                     f"<table style='font-size:19px'><tr>{head}</tr>{rows}</table></td>")
    return f"<table><tr>{''.join(cells)}</tr></table>" if cells else ""
