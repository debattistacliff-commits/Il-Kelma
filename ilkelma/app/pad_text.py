"""Local pages and Maltese text tools for Kelma Pad."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import sqlite3

from maltese_fix import DB, match_case, spelling_resources
from maltese_text import folded, key
from ilkelma.engine.learned_words import WordStore
from ilkelma.engine.maltese_fix import fix
from .learning import apply_corrections, corrected


class Pages:
    def __init__(self, folder):
        self.current = Path(folder) / "current.txt"
        self.history = self.current.parent / "history"

    def load(self):
        try:
            return self.current.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def save(self, text, *, new_page=False):
        previous = self.load()
        if text == previous and not new_page:
            return
        self.history.mkdir(parents=True, exist_ok=True)
        if previous:
            self.archive(previous)
        if new_page and text and text != previous:
            self.archive(text)
        temporary = self.current.with_suffix(".tmp")
        temporary.write_text("" if new_page else text, encoding="utf-8", newline="\n")
        temporary.replace(self.current)
        for old in sorted(self.history.glob("*.txt"), reverse=True)[20:]:
            old.unlink()

    def archive(self, text):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        (self.history / f"{stamp}-{uuid4().hex}.txt").write_text(
            text, encoding="utf-8", newline="\n")


def fix_letters(text):
    text = corrected(text, "mt")
    return fix(apply_corrections(text, WordStore().spelling()), lang="mt")


def suggestions(word, language):
    personal = corrected(word, language)
    if language != "mt":
        return [personal] if personal != word else []
    candidates = [personal, fix_letters(word)]
    pairs, known, _ = spelling_resources()
    if alternate := pairs.get(key(word)):
        if isinstance(alternate, str):
            candidates.append(match_case(word, alternate))
    with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT word FROM words WHERE fold=? ORDER BY count DESC, word LIMIT 4",
                          (folded(word),)).fetchall()
    candidates.extend(match_case(word, row[0]) for row in rows)
    if len(dict.fromkeys(candidates)) < 4:
        candidates.extend(match_case(word, item) for item in sorted(known)
                          if folded(item) == folded(word))
    return [item for item in dict.fromkeys(candidates) if item != word][:3]


def qt_position(text, index):
    """Qt counts UTF-16 units while Python counts characters."""
    return len(text[:index].encode("utf-16-le")) // 2
