import csv
import gzip
import math
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from unicodedata import normalize

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/corpora/macocu/MaCoCu-mt-en.sent.txt.gz"
OUTPUT = ROOT / "data/sentences.sqlite"
URL = re.compile(r"https?://|www\.|\b\S+@\S+|\b[\w-]+\.(?:com|org|net|mt|eu)\b", re.I)
LEGAL = re.compile(
    r"\b(?:CELEX|articles?|regulations?|directives?|paragraphs?|artikolu|artikoli|"
    r"regolament|direttiva|paragrafu)\b|^\s*\d+[.)]|\([a-z0-9]{1,3}\)|\b\d+[.)]\d+", re.I)
CHAT = re.compile(r"\b(?:i|you|we|my|your|me|us|our)\b", re.I)


def clean(text):
    text = normalize("NFC", text).replace("ʼ", "'").replace("’", "'")
    return " ".join(re.sub(r"\b([^\W\d_]+)-\s+(?=[^\W\d_])", r"\1-", text).split())


def acceptable(text):
    return (2 <= len(text.split()) <= 25 and not URL.search(text)
            and not LEGAL.search(text) and sum(c.isalpha() for c in text)
            > sum(c.isdigit() for c in text))


def everyday(en, domain):
    score = max(0, 12 - len(en.split())) / 12 + bool(CHAT.search(en)) + ("?" in en)
    if domain.endswith(".mt"):
        score += 1
    if domain == "newsbook.com.mt":
        score += 1
    if any(label in domain for label in ("eur-lex", "europarl", "dg-justice")):
        score -= 3
    if any(label in domain for label in ("jw.org", "churchofjesuschrist", "vatican", "bible", "knisja")):
        score -= 2
    return score


def build(source, output):
    temporary = output.with_suffix(".building")
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as db, gzip.open(source, "rt", encoding="utf-8", newline="") as file:
            db.executescript("""
                CREATE TABLE pairs(id INTEGER PRIMARY KEY, mt TEXT, en TEXT, domain TEXT, score REAL,
                                   confidence REAL NOT NULL, UNIQUE(mt, en));
                CREATE VIRTUAL TABLE english USING fts5(en, content='pairs', content_rowid='id',
                                                        tokenize='unicode61');
                CREATE VIRTUAL TABLE maltese USING fts5(mt, content='pairs', content_rowid='id',
                                                        tokenize='unicode61 remove_diacritics 0');
            """)
            for index, row in enumerate(csv.DictReader(file, delimiter="\t", quoting=csv.QUOTE_NONE), 1):
                if index % 100000 == 0:
                    print(f"Read {index:,} pairs", flush=True)
                try:
                    confidence = float(row["bicleaner_ai_score"])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(confidence) or confidence < .7:
                    continue
                mt, en = (clean(row[field] or "") for field in ("src_text", "trg_text"))
                if not acceptable(mt) or not acceptable(en):
                    continue
                domain = (row["domain_en"] or "").lower().removeprefix("www.")
                db.execute("""INSERT INTO pairs(mt,en,domain,score,confidence) VALUES (?,?,?,?,?)
                              ON CONFLICT(mt,en) DO UPDATE SET
                                domain=CASE WHEN excluded.score > pairs.score THEN excluded.domain ELSE pairs.domain END,
                                score=max(pairs.score,excluded.score),
                                confidence=max(pairs.confidence,excluded.confidence)""",
                           (mt, en, domain, everyday(en, domain), confidence))
            for table in ("english", "maltese"):
                db.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
            counts = db.execute("SELECT domain,count(*) FROM pairs GROUP BY domain ORDER BY count(*) DESC").fetchall()
            db.commit()
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return counts


if __name__ == "__main__":
    counts = build(SOURCE, OUTPUT)
    print(f"{sum(count for _, count in counts):,} pairs, {OUTPUT.stat().st_size:,} bytes")
    for domain, count in counts:
        print(domain, count)
