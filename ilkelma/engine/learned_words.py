"""Local word counts from comments. The database never receives comment text."""
from collections import Counter
from contextlib import ExitStack, closing
from datetime import date
from functools import lru_cache
import logging
from pathlib import Path
import re
import sqlite3
import unicodedata

from maltese_text import key

from .language import detect


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "learned_words.sqlite"
GABRA_WORDS = ROOT / "sources" / "gabra" / "words.txt"
FORM = re.compile(r"[a-zċġħżà]+(?:[-'][a-zċġħżà]+)*'?")
CHUNK = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]*://|\bmailto:|www\.)\S+"
    r"|[\w.+%-]+@[\w.-]+|[@#]\S+|(?:(?:[\w-]+\.)+[a-z]{2,}|(?:[0-9]{1,3}\.){3}[0-9]{1,3})(?:[/?:#]\S*)?"
    r"|[\w'’-]+|[.!?]+", re.IGNORECASE
)
# Pending words share a bucket by first sighting day. Rows carry only a form and a count.
BUCKET = re.compile(r"pending_[0-9]+")


def is_form(word):
    return 1 < len(word) <= 40 and FORM.fullmatch(word) is not None


def tokens(text):
    initial = True
    for match in CHUNK.finditer(unicodedata.normalize("NFC", text)):
        raw = match[0].strip("-").lstrip("'’")
        word = key(raw)
        if is_form(word):
            yield raw, word, initial, match.start(), match.end()
        initial = raw.startswith((".", "!", "?"))


def readonly(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


@lru_cache(maxsize=1)
def gabra_words():
    if GABRA_WORDS.is_file():
        return {key(line.strip()) for line in GABRA_WORDS.read_text(encoding="utf-8").splitlines()}
    return set()


def classify(text, excluded=()):
    from maltese_fix import ARTICLES, DB as corpus, MACOCU_DB, Evidence

    personal_file = ROOT / "data" / "my_words.txt"
    entries = personal_file.read_text(encoding="utf-8").splitlines() if personal_file.is_file() else []
    personal = {key(line.strip()) for line in entries if line.strip().islower()}
    personal_names = {key(line.strip()) for line in entries if line.strip().istitle()} - personal

    with ExitStack() as stack:
        db = stack.enter_context(readonly(corpus))
        macocu = stack.enter_context(readonly(MACOCU_DB)) if MACOCU_DB.is_file() else None
        evidence = Evidence(db, macocu)

        def name_like(word):
            count, titles = evidence.word(word)
            return word in evidence.title_only or word in personal_names or (
                count and titles * 2 >= count and word not in evidence.known_words
                and word not in personal and word not in gabra_words())

        def lexical(word):
            return word in personal or word in evidence.known_words or word in gabra_words()

        def known(word):
            if name_like(word) or word in excluded:
                return False
            count, _ = evidence.word(word)
            if lexical(word):
                return True
            return bool(count or db.execute("SELECT 1 FROM verified WHERE text=?", (word,)).fetchone()
                        or macocu and macocu.execute(
                            "SELECT 1 FROM words WHERE word=?", (word,)).fetchone())

        normalized = unicodedata.normalize("NFC", text)
        excluded = set(excluded)
        words = list(tokens(normalized))
        vocabulary, patterns, pending = Counter(), Counter(), set()
        index = 0
        while index < len(words):
            raw, word, initial, _, end = words[index]
            index += 1
            if word in excluded or name_like(word):
                continue
            if raw != raw.lower() and (not raw.istitle() or not initial) and not known(word):
                continue
            if word in ARTICLES and index < len(words):
                next_raw, next_word, _, start, _ = words[index]
                if (normalized[end:start].isspace() and next_raw.islower()
                        and next_word not in excluded and not name_like(next_word)):
                    change = evidence.boundary(word, next_word)
                    joined = word + next_word
                    if (change and change == word + "-" + next_word and is_form(joined)
                            and is_form(change) and known(change) and not lexical(joined)):
                        patterns[joined, change] += 1
                        index += 1
                        continue
            change = word if lexical(word) else evidence.learned_spelling(raw, personal)
            if change != word and is_form(change) and known(change):
                patterns[word, change] += 1
            elif known(word):
                vocabulary[word] += 1
            elif raw.islower():
                pending.add(word)
        return vocabulary, patterns, pending


def create_counts(db, table):
    db.execute(f"CREATE TABLE IF NOT EXISTS {table} (word TEXT PRIMARY KEY, "
               "count INTEGER NOT NULL) WITHOUT ROWID")


def expire(db, today):
    buckets = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
               if BUCKET.fullmatch(name)]
    active = []
    for table in buckets:
        if int(table.removeprefix("pending_")) <= today - 30:
            db.execute(f"DROP TABLE {table}")
        else:
            active.append(table)
    return active


def add_count(db, table, word, count):
    db.execute(f"INSERT INTO {table} VALUES (?, ?) ON CONFLICT(word) DO UPDATE "
               "SET count=count+excluded.count", (word, count))


class WordStore:
    def __init__(self, path=None):
        self.path = DB if path is None else path

    def observe(self, text, *, enabled=True, excluded=(), fallback="mt", language=None, today=None):
        if not enabled:
            return
        if language is None:
            language_text = " ".join(raw for raw, word, *_ in tokens(text) if word not in excluded)
            language = detect(language_text, default=fallback)
        if language != "mt":
            return
        vocabulary, patterns, pending = classify(text, excluded)
        if not (vocabulary or patterns or pending):
            return
        day = (today or date.today()).toordinal()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            buckets = expire(db, day)
            create_counts(db, "vocabulary")
            db.execute("CREATE TABLE IF NOT EXISTS spelling_patterns ("
                       "wrong_word TEXT, right_word TEXT, count INTEGER NOT NULL, "
                       "PRIMARY KEY(wrong_word, right_word)) WITHOUT ROWID")
            for word, count in vocabulary.items():
                add_count(db, "vocabulary", word, count)
            db.executemany("INSERT INTO spelling_patterns VALUES (?, ?, ?) "
                           "ON CONFLICT DO UPDATE SET count=count+excluded.count",
                           ((wrong, right, count) for (wrong, right), count in patterns.items()))
            for word in pending:
                if db.execute("SELECT 1 FROM vocabulary WHERE word=?", (word,)).fetchone():
                    add_count(db, "vocabulary", word, 1)
                    continue
                for table in buckets:
                    row = db.execute(f"SELECT count FROM {table} WHERE word=?", (word,)).fetchone()
                    if row:
                        if row[0] >= 4:
                            add_count(db, "vocabulary", word, row[0] + 1)
                            db.execute(f"DELETE FROM {table} WHERE word=?", (word,))
                        else:
                            add_count(db, table, word, 1)
                        break
                else:
                    table = f"pending_{day}"
                    if table not in buckets:
                        create_counts(db, table)
                        buckets.append(table)
                    add_count(db, table, word, 1)
        return sum(vocabulary.values()) + sum(patterns.values()) + len(pending)

    def cleanup(self, today=None):
        if self.path.is_file():
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA secure_delete=ON")
                db.execute("BEGIN IMMEDIATE")
                expire(db, (today or date.today()).toordinal())

    def forget(self):
        if self.path.is_file():
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA secure_delete=ON")
                db.execute("BEGIN IMMEDIATE")
                for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    if name in {"vocabulary", "spelling_patterns"} or BUCKET.fullmatch(name):
                        db.execute(f"DROP TABLE {name}")

    def hints(self):
        return tuple(word for word, in self.read(
            "SELECT word FROM vocabulary WHERE count >= 2 ORDER BY count DESC, word LIMIT 40"))

    def patterns(self):
        choices = {}
        for wrong, right, count in self.read(
                "SELECT wrong_word, right_word, count FROM spelling_patterns "
                "ORDER BY wrong_word, count DESC, right_word"):
            if wrong not in choices:
                choices[wrong] = right, count
            elif choices[wrong][1] == count:
                choices[wrong] = None, count
        return {wrong: right for wrong, (right, _) in choices.items() if right}

    def spelling(self):
        """Learned patterns plus the words from the Learn book; learned ones win."""
        from ilkelma.learn import book_patterns
        return book_patterns() | self.patterns()

    def read(self, sql):
        if self.path.is_file():
            try:
                with readonly(self.path) as db:
                    return db.execute(sql).fetchall()
            except sqlite3.Error as error:
                if not str(error).startswith("no such table:"):
                    logging.warning("Could not read learned words")
        return []
