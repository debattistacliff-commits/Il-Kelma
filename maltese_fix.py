"""Conservative Maltese spelling fixes for speech transcripts."""

from contextlib import ExitStack, closing
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import sqlite3

from maltese_text import WORD, detect_language, folded, key, macocu_fold


DATA = Path(__file__).resolve().parent / "data"
DB = DATA / "maltese_words.sqlite"
MACOCU_DB = DATA / "macocu_words.sqlite"
HUNSPELL = DATA.parent / "sources" / "hunspell-mt" / "mt.dic"
SENTENCE = re.compile(r"[^.!?\r\n]+")
ARTICLES = set("il l it id ir is ix iż iċ in tal ta' tel bil fil mill mal sal għall".split())


@lru_cache(maxsize=1)
def spelling_resources():
    pairs_file = DATA / "spell_mt_pairs.json"
    basics_file = DATA / "maltese_basics.json"
    pairs = json.loads(pairs_file.read_text(encoding="utf-8")) if pairs_file.is_file() else {}
    basics = json.loads(basics_file.read_text(encoding="utf-8")) if basics_file.is_file() else {}
    words = {key(entry["maltese"]) for entry in basics.get("pronouns", [])}
    title_only = set()
    if HUNSPELL.is_file():
        # This edition lists full forms and its affix file has no inflection rules.
        with HUNSPELL.open(encoding="utf-8") as file:
            next(file)
            forms = [line.strip().split("/", 1)[0] for line in file if line.strip()]
        lower = {key(form) for form in forms if form.islower()}
        title_only = {key(form) for form in forms if form != form.lower()} - lower - words
        words.update(lower | title_only)
    return pairs, words, title_only


def match_case(original, corrected):
    if len(original) > 1 and original.isupper():
        return corrected.upper()
    if len(original) == len(corrected) and macocu_fold(original) == macocu_fold(corrected):
        return "".join(new.upper() if old.isupper() else new
                       for old, new in zip(original, corrected))
    if original[0].isupper():
        return corrected[0].upper() + corrected[1:]
    return corrected


class Evidence:
    def __init__(self, db, macocu=None, preferred=()):
        self.db = db
        self.macocu = macocu
        self.preferred = set(preferred)
        self.replacements, self.known_words, self.title_only = spelling_resources()
        self.word = lru_cache(maxsize=4096)(self.word)
        self.pair = lru_cache(maxsize=4096)(self.pair)
        self.spelling = lru_cache(maxsize=4096)(self.spelling)

    def word(self, word):
        row = self.db.execute("SELECT count, title_count FROM words WHERE word=?",
                              (word,)).fetchone()
        return row or (0, 0)

    def pair(self, first, second):
        row = self.db.execute("SELECT count FROM bigrams WHERE first=? AND second=?",
                              (first, second)).fetchone()
        return row[0] if row else 0

    def spelling(self, original, minimum=10, ratio=16):
        base = key(original)
        choices = self.db.execute(
            """SELECT word, count FROM words WHERE fold=?
               ORDER BY count DESC, word LIMIT ?""", (folded(base), 8 if self.preferred else 2)
        ).fetchall()
        if base not in self.preferred and self.preferred and choices:
            best, count = choices[0]
            # A familiar form can break a close contest with the input form.
            if best == base:
                for candidate, frequency in choices[1:]:
                    if frequency * 1.15 < count:
                        break
                    if candidate in self.preferred and frequency >= minimum:
                        return candidate
        if choices:
            best, count = choices[0]
            runner = choices[1][1] if len(choices) > 1 else 0
            if count >= max(minimum, ratio * runner):
                return best
        if self.macocu is None or base != macocu_fold(base):
            return base
        try:
            choices = self.macocu.execute(
                "SELECT word, count FROM words WHERE fold=? ORDER BY count DESC, word LIMIT 2",
                (macocu_fold(base),),
            ).fetchall()
        except sqlite3.Error:
            logging.exception("Could not read MaCoCu word counts")
            self.macocu = None
            return base
        if choices:
            best, count = choices[0]
            runner = choices[1][1] if len(choices) == 2 else 0
            if best != macocu_fold(best) and count >= max(50, 20 * runner):
                return best
        return base

    def learned_spelling(self, original, personal):
        base = key(original)
        candidate = self.replacements.get(base)
        if (candidate and base not in self.known_words and candidate in self.known_words
                and (original.islower() or original.istitle())
                and not personal.intersection(re.split("[-']", candidate))):
            return candidate
        return self.spelling(original)

    def boundary(self, first, second=None):
        if second is None:
            joined = first
            baseline = self.word(first)[0]
            old_position = None
        else:
            joined = first + second
            split = self.pair(first, second)
            fragment = min(self.word(first)[0], self.word(second)[0])
            baseline = max(split, fragment)
            old_position = len(first)
            if first in ARTICLES:
                article = first + "-" + second
                count, titles = self.word(article)
                if count >= max(10, 8 * split):
                    if titles * 2 > count and self.word(second)[1] * 2 > self.word(second)[0]:
                        article = first + "-" + second.capitalize()
                    return article
            count = self.word(joined)[0]
            # A few corpus typos can raise a fragment above the old cutoff.
            if (count >= max(20, 12 * baseline)
                    and (fragment <= 2 or split <= 2 and count >= 100 * baseline)):
                return joined
        if baseline > 2:
            return None
        options = []
        for position in range(2, len(joined) - 1):
            if position == old_position:
                continue
            left = self.spelling(joined[:position])
            right = self.spelling(joined[position:])
            count = self.pair(left, right)
            if count >= max(4, 8 * baseline):
                options.append((count, left + " " + right))
        # The reported greeting is attested as two words individually.
        # Require a verified greeting and a clearly established second word.
        if (first == "bon" and second == "ġuħabib"
                and self.word("bonġu")[0] >= 4 and self.word("ħabib")[0] >= 20
                and self.db.execute(
                    "SELECT count(*) FROM verified WHERE text IN ('bonġu', 'ħabib')"
                ).fetchone()[0] == 2):
            return "bonġu ħabib"
        if len(options) == 1:
            return options[0][1]
        return None


def fix_sentence(sentence, evidence, personal, lang):
    # Sentence-level gating keeps English and Italian away from Maltese edits.
    language_text = " ".join(match[0] for match in WORD.finditer(sentence)
                             if key(match[0]) not in personal)
    language = detect_language(language_text, default=lang)
    if language in {"en", "it"} and key(language_text) != "il malti":
        return sentence
    # Repair the duplicated greeting only before its recognized addressee.
    sentence = re.sub(r"^(\s*)(bon[ġg]) bon[ġg]i(?= ħabib\b)",
                      lambda match: match[1] + match_case(match[2], "bonġu"),
                      sentence, flags=re.IGNORECASE)
    tokens = list(WORD.finditer(sentence))
    if language is None and key(language_text) != "il malti":
        def dominant(match):
            word = key(match[0])
            if word != folded(word) or personal.intersection(re.split("[-']", word)):
                return match[0]
            candidate = evidence.spelling(word, 50, 20)
            return match_case(match[0], candidate) if candidate != macocu_fold(candidate) else match[0]

        return WORD.sub(dominant, sentence)
    parts = []
    cursor = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        original = token[0]
        parts.append(sentence[cursor:token.start()])
        cursor = token.end()
        if personal.intersection(re.split("[-']", key(original))):
            parts.append(original)
            index += 1
            continue
        word = evidence.learned_spelling(original, personal)
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if (following and sentence[cursor:following.start()] == " "
                and not personal.intersection(re.split("[-']", key(following[0])))
                and (following[0].islower() or word in ARTICLES and following[0].istitle())):
            change = evidence.boundary(word, evidence.spelling(following[0]))
            if change:
                parts.append(match_case(original, change))
                cursor = following.end()
                index += 2
                continue
        change = evidence.boundary(word)
        if word == "bil-malti":
            count, titles = evidence.word(word)
            if count >= 10 and titles * 4 >= count * 3:
                change = "bil-Malti"
        parts.append(match_case(original, change or word) if change or word != key(original)
                     else original)
        index += 1
    parts.append(sentence[cursor:])
    return "".join(parts)


def fix(text, *, lang=None, use_macocu=True):
    """Return the transcript with well-supported Maltese corrections."""
    if lang in {"en", "it"}:
        return text
    personal = {key(line.strip()) for line in (DATA / "my_words.txt").read_text(
        encoding="utf-8").splitlines() if line.strip()}
    with ExitStack() as resources:
        db = resources.enter_context(closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)))
        macocu = None
        if use_macocu and MACOCU_DB.is_file():
            try:
                macocu = resources.enter_context(closing(sqlite3.connect(
                    MACOCU_DB.as_uri() + "?mode=ro", uri=True)))
            except sqlite3.Error:
                logging.exception("Could not open MaCoCu word counts")
        from ilkelma.engine.learned_words import WordStore

        evidence = Evidence(db, macocu, WordStore().hints() if lang == "mt" else ())
        try:
            return SENTENCE.sub(lambda match: fix_sentence(match[0], evidence, personal, lang), text)
        finally:
            evidence.word.cache_clear()
            evidence.pair.cache_clear()
            evidence.spelling.cache_clear()
