from ilkelma.languages import packs
from maltese_text import detect_language, key, WORD


def detect(text, default="en"):
    words = {key(match[0]) for match in WORD.finditer(text)}
    scores = {code: len(words.intersection(pack["common_words"].split()))
              for code, pack in packs().items()}
    strongest = max(scores.values(), default=0)
    winner = [code for code, score in scores.items() if score == strongest]
    if strongest and len(winner) == 1:
        return winner[0]
    return detect_language(text, default=default)
