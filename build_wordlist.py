import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unicodedata

from maltese_text import WORD, folded, key


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/maltese_words.sqlite"
MEMORY = Path("K:/Titan_Memory")
LIBRARY = MEMORY / "maltese_library"
CORPUS = LIBRARY / "12_external_corpora/korpus_malti/data"
TREEBANK = LIBRARY / "14_treebanks"
BANK = MEMORY / "maltese_language_bank"
GENRES = ("blogs", "comics", "press_mt", "press_eu", "belles_lettres", "parliament")
BATCH = 72 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    word TEXT PRIMARY KEY, fold TEXT NOT NULL, count INTEGER NOT NULL,
    title_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS words_fold ON words(fold);
CREATE TABLE IF NOT EXISTS bigrams (
    first TEXT, second TEXT, count INTEGER NOT NULL,
    PRIMARY KEY(first, second)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS verified (text TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS progress (
    file TEXT PRIMARY KEY, genre TEXT NOT NULL, size INTEGER NOT NULL,
    modified INTEGER NOT NULL, offset INTEGER NOT NULL
) WITHOUT ROWID;
"""


def count_sentence(sentence, words, titles, pairs):
    sentence = unicodedata.normalize("NFC", sentence)
    previous = None
    end = 0
    for match in WORD.finditer(sentence):
        spelling = match[0]
        lowercase = spelling.lower()
        word = lowercase.replace("’", "'")
        words[word] += 1
        # Skip sentence starts when estimating the normal case of a word.
        if previous is not None and lowercase != spelling and any(char.isupper() for char in spelling):
            titles[word] += 1
        if previous is not None and sentence[end:match.start()].isspace():
            pairs[previous, word] += 1
        previous, end = word, match.end()


def save_batch(db, updates, words, titles, pairs):
    with db:
        db.execute("BEGIN IMMEDIATE")
        for file_id, genre, stat, start, offset in updates:
            saved = db.execute("SELECT offset FROM progress WHERE file=?", (file_id,)).fetchone()
            if (saved[0] if saved else 0) != start:
                raise ValueError(f"Another build updated {file_id}. Run the builder again to resume.")
        db.executemany(
            """INSERT INTO words VALUES (?, ?, ?, ?) ON CONFLICT(word) DO UPDATE SET
               count=count+excluded.count, title_count=title_count+excluded.title_count""",
            ((word, folded(word), words[word], titles[word]) for word in sorted(words)),
        )
        db.executemany(
            """INSERT INTO bigrams VALUES (?, ?, ?) ON CONFLICT(first, second)
               DO UPDATE SET count=count+excluded.count""",
            ((first, second, pairs[first, second]) for first, second in sorted(pairs)),
        )
        db.executemany(
            """INSERT INTO progress VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(file) DO UPDATE SET offset=excluded.offset""",
            ((file_id, genre, stat.st_size, stat.st_mtime_ns, offset)
             for file_id, genre, stat, start, offset in updates),
        )


class Batch:
    def __init__(self, db):
        self.db = db
        self.reset()

    def reset(self):
        self.words, self.titles, self.pairs = Counter(), Counter(), Counter()
        self.updates = []
        self.size = 0

    def flush(self):
        if self.updates:
            save_batch(self.db, self.updates, self.words, self.titles, self.pairs)
            self.reset()


def import_file(batch, path, genre):
    file_id = f"{genre}/{path.name}"
    stat = path.stat()
    saved = batch.db.execute("SELECT size, modified, offset FROM progress WHERE file=?",
                       (file_id,)).fetchone()
    if saved and saved[:2] != (stat.st_size, stat.st_mtime_ns):
        raise ValueError(f"Source changed: {path}. Use a fresh database.")
    offset = saved[2] if saved else 0
    if saved and offset == stat.st_size:
        return
    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            start = stream.tell()
            while batch.size + stream.tell() - start < BATCH:
                line = stream.readline()
                if not line:
                    break
                if genre != "MUDT":
                    sentences = json.loads(line)["text"] if line.strip() else ()
                    if isinstance(sentences, str):
                        sentences = (sentences,)
                else:
                    sentences = (line[9:].decode("utf-8"),) if line.startswith(b"# text = ") else ()
                for sentence in sentences:
                    count_sentence(sentence, batch.words, batch.titles, batch.pairs)
            offset = stream.tell()
            batch.updates.append((file_id, genre, stat, start, offset))
            batch.size += offset - start
            if batch.size >= BATCH:
                batch.flush()
            if offset < stat.st_size:
                print(f"{file_id}: {offset * 100 // stat.st_size}%", flush=True)
            if offset == stat.st_size:
                break


def import_bank(db):
    sources = (
        ("lexicon/entries.json", "entries", ("maltese",)),
        ("expressions/entries.json", "entries", ("maltese",)),
        ("conversation/patterns.json", "patterns", ("user_examples", "natural_response_patterns")),
        ("grammar/rules.json", "rules", ("maltese", "examples")),
    )
    verified = set()
    for name, group, fields in sources:
        with (BANK / name).open(encoding="utf-8") as stream:
            for entry in json.load(stream)[group]:
                if entry.get("status") not in {"approved", "verified"}:
                    continue
                for field in fields:
                    values = entry.get(field, [])
                    if isinstance(values, str):
                        values = [values]
                    verified.update(key(value.strip()) for value in values
                                    if isinstance(value, str) and value.strip())
    with db:
        db.execute("DELETE FROM verified")
        db.executemany("INSERT INTO verified VALUES (?)", ((value,) for value in verified))


def main():
    parser = argparse.ArgumentParser(description="Build or resume the local Maltese word list.")
    parser.add_argument("--genres", nargs="+", default=None)
    parser.add_argument("--max-files-per-genre", type=int, help="Read only the first N files in each genre.")
    args = parser.parse_args()
    if args.max_files_per_genre is not None and args.max_files_per_genre < 1:
        parser.error("--max-files-per-genre must be at least 1")
    available = {folder.name for folder in CORPUS.iterdir() if folder.is_dir()}
    # MLRS publishes the same sentences again in the shuffled configuration.
    # Count each genre once so the shuffled copy cannot double its weight.
    args.genres = args.genres or sorted(available - {"shuffled"}, key=lambda name: (name not in GENRES, name))
    for genre in args.genres:
        if genre not in available:
            parser.error(f"Unknown genre: {genre}")
        if genre == "shuffled":
            parser.error("shuffled repeats the genre data")
    DB.parent.mkdir(exist_ok=True)
    # SQLite requires a writable database for counting. Only this project file
    # is opened for writing. The source drives supply plain text files.
    with closing(sqlite3.connect(DB.as_uri() + "?mode=rwc", uri=True)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA cache_size=-1048576")
        db.execute("PRAGMA wal_autocheckpoint=131072")
        db.executescript(SCHEMA)
        import_bank(db)
        groups = [("MUDT", sorted(TREEBANK.glob("*.conllu")))]
        groups.extend(
            (genre, sorted((CORPUS / genre).glob("*.jsonl"))[:args.max_files_per_genre])
            for genre in args.genres
        )
        for genre, files in groups:
            batch = Batch(db)
            if not files:
                print(f"{genre}: no files", flush=True)
            for number, path in enumerate(files, 1):
                if number == 1 or number % 10 == 0 or number == len(files):
                    print(f"{genre}: file {number}/{len(files)}", flush=True)
                import_file(batch, path, genre)
                if path.stat().st_size >= 32 * BATCH:
                    print(f"Finished {path.name}", flush=True)
            batch.flush()
        print("Words:", db.execute("SELECT count(*) FROM words").fetchone()[0])
        print("Bigrams:", db.execute("SELECT count(*) FROM bigrams").fetchone()[0])
        for row in db.execute("SELECT genre, count(*), sum(offset) FROM progress GROUP BY genre"):
            print(*row, sep=" | ")


if __name__ == "__main__":
    main()
