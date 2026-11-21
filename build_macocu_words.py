from collections import Counter
from contextlib import closing
import re
import sqlite3
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from build_sentence_bank import ROOT
from maltese_text import WORD, macocu_fold

SOURCE = ROOT / "data/corpora/macocu/MaCoCu-mt-2.0.xml.zip"
OUTPUT = ROOT / "data/macocu_words.sqlite"
REMOVE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+")
TRANSLATE = str.maketrans("ʼ’", "''")


def flush(db, counts):
    db.executemany("""INSERT INTO words VALUES (?,?,?)
                      ON CONFLICT(word) DO UPDATE SET count=count+excluded.count""",
                   ((word, macocu_fold(word), count) for word, count in counts.items()))
    db.commit()
    counts.clear()


def build(source, output):
    temporary = output.with_suffix(".building")
    temporary.unlink(missing_ok=True)
    try:
        with ZipFile(source) as archive, archive.open(next(
            name for name in archive.namelist() if name.endswith(".xml")
        )) as file, closing(sqlite3.connect(temporary)) as db:
            db.execute("CREATE TABLE words(word TEXT PRIMARY KEY, fold TEXT, count INTEGER) WITHOUT ROWID")
            counts = Counter()
            stack = []
            paragraphs = tokens = 0
            for event, element in ET.iterparse(file, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if element.tag == "p" and element.get("lang") == "mt":
                    text = REMOVE.sub(" ", "".join(element.itertext())).lower().translate(TRANSLATE)
                    words = WORD.findall(text)
                    tokens += len(words)
                    counts.update(words)
                    paragraphs += 1
                    if paragraphs % 100000 == 0:
                        print(f"{paragraphs:,} Maltese paragraphs, {tokens:,} words", flush=True)
                    if len(counts) >= 250000:
                        flush(db, counts)
                stack.pop()
                if stack and element.tag in {"p", "doc"}:
                    stack[-1].remove(element)
            flush(db, counts)
            db.execute("CREATE INDEX words_fold ON words(fold, count DESC, word)")
            forms = db.execute("SELECT count(*) FROM words").fetchone()[0]
            db.commit()
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{paragraphs:,} Maltese paragraphs, {tokens:,} words, {forms:,} forms, "
          f"{output.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    build(SOURCE, OUTPUT)
