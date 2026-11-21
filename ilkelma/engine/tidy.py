import re
from pathlib import Path

from maltese_text import WORD, key

MY_WORDS = Path(__file__).resolve().parents[2] / "data" / "my_words.txt"
QUESTION_START = re.compile(r"^(?:kif|fejn|meta|għaliex|min|kemm)\b|^x['’]", re.IGNORECASE)
QUESTION_END = {"hux", "hijiex", "mhux"}
GREETING = re.compile(r"(^|(?<=[.!?]) )([“\"(]*(?:bonġu|bonswa|ħello)) (?=[^\W\d_])", re.IGNORECASE)
GREETING_QUESTION = re.compile(
    r"(^|(?<=[.!?]) |(?<=,) )([“\"(]*kif (?:inti|int|aħna))[,]? "
    r"(?=(?:illum|issa|jien|jiena|aħna|għada|ilbieraħ|bħalissa|imbagħad|qed)\b)",
    re.IGNORECASE,
)


def tidy(text):
    """Trim transcript spacing without rewriting its words."""
    return " ".join(text.split())


def collapse_repeats(text):
    tokens = text.split()
    words = [token.lower() for token in tokens]
    stops = ".!?…"
    index = 0
    while index + 5 < len(tokens):
        for size in range(3, (len(tokens) - index) // 2 + 1):
            end = index + size
            phrase = words[index:end - 1] + [words[end - 1].rstrip(stops)]
            copy = words[end:end + size - 1] + [words[end + size - 1].rstrip(stops)]
            if phrase != copy or not all(WORD.fullmatch(word) for word in phrase):
                continue
            if any(phrase == [phrase[i % period] for i in range(size)]
                   for period in (1, 2) if size % period == 0):
                continue
            # Keep punctuation from the final copy and casing from the first.
            last = tokens[end + size - 1]
            bare = tokens[end - 1].rstrip(stops)
            ending = last[len(last.rstrip(stops)):] or tokens[end - 1][len(bare):]
            tokens[end - 1] = bare + ending
            words[end - 1] = tokens[end - 1].lower()
            del tokens[end:end + size]
            del words[end:end + size]
            break
        else:
            index += 1
    return " ".join(tokens)


def punctuate(text, language, personal_file=MY_WORDS):
    text = tidy(text)
    if not text:
        return ""
    names = {key(name): name for name in ("Malti", "Malta")}
    if personal_file.is_file():
        names.update((key(line), line) for value in personal_file.read_text(encoding="utf-8").splitlines()
                     if (line := value.strip()) and WORD.fullmatch(line))

    def restore_case(match):
        word = match[0]
        if key(word) in names:
            return names[key(word)]
        return re.sub(r"[^\W\d_]+", lambda part: names.get(key(part[0]), part[0]), word)

    text = WORD.sub(restore_case, text)
    if language == "mt":
        text = GREETING.sub(r"\1\2, ", text)
        text = GREETING_QUESTION.sub(r"\1\2? ", text)
        text = re.sub(r"(\bħafna) (?=u ħa\b)", r"\1, ", text, flags=re.IGNORECASE)
    clauses = re.split(r"(?<=[.!?…])\s+", text)
    for index, clause in enumerate(clauses):
        words = WORD.findall(clause)
        question = language == "mt" and words and (
            QUESTION_START.search(clause.lstrip("\"“("))
            or ("," in clause and QUESTION_START.search(clause.rsplit(",", 1)[-1].strip()))
            or words[-1].casefold() in QUESTION_END
        )
        core = clause.rstrip("\"”’)]")
        suffix = clause[len(core):]
        if question and not core.endswith(("?", "!", "…", "...")):
            clause = core.rstrip(".,:;") + "?" + suffix
        elif not core.endswith((".", "?", "!", "…")):
            clause = core.rstrip(",:;") + "." + suffix
        clauses[index] = re.sub(r"^[^\w]*[^\W\d_]", lambda match: match[0].upper(), clause)
    return " ".join(clauses)
