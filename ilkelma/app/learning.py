from dataclasses import dataclass
import logging
import re
import sqlite3
import unicodedata

from maltese_text import folded, key

from ilkelma.engine.maltese_fix import fix
from ilkelma.engine.sentence_bank import lookup
from ilkelma.engine.phrasing import LANGUAGES, generate, one_sentence

from . import storage

LANGUAGE_TAIL = re.compile(
    r"[, ]+(?:in (?:maltese|english|italian)|bil-malti|bl-ingliż|bit-taljan|"
    r"in (?:maltese|inglese|italiano))[.!?]*$", re.IGNORECASE)


def phrase_key(text):
    text = LANGUAGE_TAIL.sub("", key(text.strip()))
    return " ".join(re.findall(r"\w+(?:[-']\w+)*", text))


def aliases(entry):
    return {phrase_key(value) for value in
            [*(entry[code] for code in LANGUAGES), *entry.get("aliases", [])]}


def remembered(book, idea, language):
    token = phrase_key(idea)
    if not token:
        return None
    approved = book.get("confirmed", {}).get(language, {})
    if token in approved:
        return approved[token]
    entries = book.get("seed", [])
    matches = {entry[language] for entry in entries if token in aliases(entry)}
    if matches:
        return matches.pop() if len(matches) == 1 else None
    # Only match spelling variants here. A missing or different word can change the meaning.
    matches = {value for alias, value in approved.items() if folded(alias) == folded(token)}
    if len(matches) == 1:
        return matches.pop()
    if not matches:
        matches = {entry[language] for entry in entries
                   if any(folded(alias) == folded(token) for alias in aliases(entry))}
    return matches.pop() if len(matches) == 1 else None


def suggest(idea, language, model, *, corrected_idea=None):
    return suggest_options(idea, language, model, corrected_idea=corrected_idea,
                           use_bank=False)[0].text


@dataclass(frozen=True)
class Suggestion:
    text: str
    source: str


def suggest_options(idea, language, model, *, corrected_idea=None, use_bank=True):
    book = storage.load("phrasebook", {})
    approved = book.get("confirmed", {}).get(language, {}).get(phrase_key(idea))
    if approved:
        return [Suggestion(approved, "Phrasebook")]
    spoken = corrected_idea if corrected_idea is not None else idea
    text = remembered(book, spoken, language)
    if text is not None:
        text = fix(one_sentence(text), lang="mt") if language == "mt" else one_sentence(text)
        return [Suggestion(one_sentence(corrected(text, language)), "Phrasebook")]
    try:
        examples = (lookup(storage.DATA / "sentences.sqlite", LANGUAGE_TAIL.sub("", spoken.strip()),
                           language) if use_bank else [])
    except sqlite3.Error:
        logging.exception("Could not look up MaCoCu sentences")
        examples = []
    options = {}
    for pair in examples:
        if pair.exact:
            text = corrected(getattr(pair, language), language)
            if not text.endswith((".", "?", "!")):
                text += "."
            options.setdefault(phrase_key(text), Suggestion(text, "Real Maltese example"))
    if options:
        return list(options.values())[:3]
    kwargs = {"examples": examples} if examples else {}
    text = one_sentence(corrected(generate(spoken, language, model, **kwargs), language))
    return [Suggestion(text, "Suggested sentence")]


def spoken_option(text):
    commands = {"one": 0, "wieħed": 0, "wiehed": 0, "1": 0,
                "two": 1, "tnejn": 1, "2": 1,
                "three": 2, "tlieta": 2, "3": 2}
    return commands.get(key(text.strip().rstrip(".!?")))


def confirm_phrase(idea, language, text):
    book = storage.load("phrasebook", {})
    book.setdefault("confirmed", {}).setdefault(language, {})[phrase_key(idea)] = text
    storage.save("phrasebook", book)


def apply_corrections(text, mapping):
    if not mapping:
        return text
    text = unicodedata.normalize("NFC", text)
    variants = [re.escape(word).replace("'", "['’]") for word in sorted(mapping, key=len, reverse=True)]
    pattern = r"(?<!\w)(?<!\w[-'’])(?:" + "|".join(variants) + r")(?!\w|[-'’]\w)"

    def replace(match):
        heard = match[0]
        correct = mapping.get(key(heard), heard)
        if len(heard) > 1 and heard.isupper():
            return correct.upper()
        if heard[0].isupper():
            return correct[0].upper() + correct[1:]
        return correct

    return re.sub(pattern, replace, text, flags=re.IGNORECASE)


def corrected(text, language):
    return apply_corrections(text, storage.load("my_corrections", {}).get(language, {}))


def learn_word(language, heard, correct, *, name=False):
    heard, correct = key(heard.strip()), unicodedata.normalize("NFC", correct.strip())
    if language not in LANGUAGES or not heard or not correct or not any(c.isalnum() for c in heard):
        raise ValueError("Enter the words you heard and the corrected words.")
    if name:
        path = storage.DATA / "my_words.txt"
        words = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if key(correct) not in {key(word) for word in words}:
            temporary = path.with_suffix(".tmp")
            temporary.write_text("\n".join([*words, correct]) + "\n", encoding="utf-8", newline="\n")
            temporary.replace(path)
    corrections = storage.load("my_corrections", {})
    corrections.setdefault(language, {})[heard] = correct
    storage.save("my_corrections", corrections)
    return {heard: correct}
