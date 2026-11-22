"""Words from the Learn book that Il-Kelma should always spell right."""
from functools import lru_cache
import json
from pathlib import Path
import re

BOOK = Path(__file__).with_name("maltese.json")
PLAIN = str.maketrans({"ċ": "c", "ġ": "g", "ħ": "h", "ż": "z"})


def book_words(path=BOOK):
    words = set()

    def walk(value):
        if isinstance(value, str):
            words.update(re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", value.casefold()))
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for name, item in value.items():
                if name not in ("meaning_en", "en", "id", "icon", "note"):
                    walk(item)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return words


@lru_cache
def book_patterns(path=BOOK):
    """Plain spelling -> right spelling, e.g. 'tihdux' -> 'tiħdux'.
    A plain form that is itself a real word in the book (hu, he) is left alone."""
    words = book_words(path)
    patterns = {}
    for word in words:
        plain = word.translate(PLAIN)
        if plain != word and plain not in words:
            patterns[plain] = word
    return patterns
