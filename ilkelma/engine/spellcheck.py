"""Find Maltese words that are probably misspelled: not in the MLRS word data."""
from contextlib import closing
from functools import lru_cache
import re
import sqlite3

from maltese_fix import DB

from .maltese_fix import fix

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?['’]?")


@lru_cache(maxsize=20000)
def known(word, path=DB):
    word = word.casefold().replace("’", "'")
    forms = {word, word.rstrip("'")}
    if not path.is_file():
        return True
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return any(db.execute("SELECT 1 FROM words WHERE word=? AND count>=2", (form,)).fetchone()
                   for form in forms if form)


def unknown_spans(text, extra=()):
    """(start, end) of each word not found, so the Pad can underline it."""
    extra = {word.casefold() for word in extra}
    spans = []
    for match in WORD.finditer(text):
        word = match[0]
        if len(word.strip("'’")) < 2 or word.casefold() in extra:
            continue
        start, end = match.span()
        if not known(word) or missing_letters(word):
            spans.append((start, end))
    return spans


@lru_cache(maxsize=20000)
def missing_letters(word):
    """True when the letter fixer would write the word differently (hadt -> ħadt)."""
    return fix(word, lang="mt").casefold() != word.casefold()
