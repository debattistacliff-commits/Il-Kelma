"""Retrieve attested bilingual examples from the local MaCoCu bank."""

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
import re
import sqlite3

from maltese_text import key

STOP = set("a an the to of in for is it and on at i you my me your we will would can could "
           "be am was are do have s t il l ta u li".split())
ITALIAN = {
    "aiuto": "help", "amico": "friend", "amica": "friend", "aspetta": "wait",
    "auguri": "wishes", "bisogno": "need", "buongiorno": "morning", "casa": "home",
    "cena": "dinner", "chiama": "call", "chiavi": "keys", "ciao": "hello",
    "compleanno": "birthday", "domani": "tomorrow", "dove": "where",
    "festa": "party", "finestra": "window", "grazie": "thanks", "mamma": "mum",
    "oggi": "today", "per": "for", "piacere": "please", "puoi": "can you",
    "ritardo": "late", "stasera": "tonight", "tardi": "late", "telefono": "phone",
    "vediamo": "see", "venire": "come",
}


@dataclass(frozen=True)
class Pair:
    mt: str
    en: str
    domain: str
    score: float
    exact: bool


def words(text):
    text = key(text).replace("ʼ", "'")
    return re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text)


def fts_terms(text):
    # unicode61 splits compounds and apostrophes.
    return re.findall(r"[^\W_]+", key(text).replace("ʼ", "'"))


def lookup(path, idea, language, limit=5):
    original = words(idea)
    if not original or not path.is_file():
        return []
    gloss = " ".join(ITALIAN.get(word, "") for word in original)
    full = fts_terms(idea)[:25]
    candidates = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        for table, (column, query) in {"english": ("en", gloss), "maltese": ("mt", "")}.items():
            related = list(dict.fromkeys(fts_terms(idea + " " + query)))
            content = [term for term in related if term not in STOP][:12] or full[:3]
            exact_phrase = '"' + " ".join(full) + '"'
            searches = [exact_phrase, " AND ".join(content), " OR ".join(content)]
            query_words = Counter(words(idea + " " + query))
            query_content = set(query_words) - STOP
            original_numbers = re.findall(r"\d+", idea)
            for expression in dict.fromkeys(searches):
                for mt, en, domain, everyday, confidence, rank in db.execute(f"""
                    WITH shortlist AS MATERIALIZED (
                        SELECT rowid,rank FROM {table}
                        WHERE {table} MATCH ? ORDER BY rank LIMIT 100
                    )
                    SELECT p.mt,p.en,p.domain,p.score,p.confidence,s.rank
                    FROM shortlist s JOIN pairs p ON p.id=s.rowid
                """, (expression,)):
                    side = mt if column == "mt" else en
                    side_words = words(side)
                    question = side.rstrip(" \"”’'").endswith("?")
                    exact = (language != "it" and confidence >= .85 and original == side_words
                             and (not idea.rstrip().endswith("?") or question)
                             and mt.rstrip(" \"”’'").endswith("?") == en.rstrip(" \"”’'").endswith("?"))
                    shared = sum((query_words & Counter(side_words)).values())
                    side_content = set(side_words) - STOP
                    topical = len(query_content & side_content)
                    if not exact and (not topical or len(side_words) > len(original) + 8
                                      or any(n not in original_numbers for n in re.findall(r"\d+", mt + " " + en))):
                        continue
                    overlap = 2 * shared / (query_words.total() + len(side_words))
                    topical_overlap = 2 * topical / max(1, len(query_content) + len(side_content))
                    score = overlap * 5 + topical_overlap * 8 + min(-rank, 45) * .035 + everyday * .35
                    pair = Pair(mt, en, domain, score, exact)
                    previous = candidates.get((mt, en))
                    if previous is None or (pair.exact, pair.score) > (previous.exact, previous.score):
                        candidates[mt, en] = pair
    return sorted(candidates.values(), key=lambda pair: (pair.exact, pair.score), reverse=True)[:limit]
