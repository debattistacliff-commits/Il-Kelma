"""Prepare individual OCR comments for speech."""
from contextlib import closing
import re
import sqlite3

from maltese_fix import DB, match_case
from maltese_text import EN_MARKERS, IT_MARKERS, MT_MARKERS, WORD, key

from .screen_ocr import prepare_maltese


MT_WORDS = MT_MARKERS | set(
    "u li ta il f ma kif fejn fejna għax issa sabiħa sabiha ħabib grazzi "
    "jien aħna int iva le xi xejn mela prosit ħafna dan din illum għada".split()
)
EN_WORDS = EN_MARKERS | set(
    "hi hey everyone everybody lovely love am an a be been all as at by clear "
    "could thank thanks great nice yes no ok it it's its our us me him her them "
    "what when where why who which out up down so if then too very some more "
    "see get want know feel happy night welcome going i'm don't".split()
)
IT_WORDS = IT_MARKERS | set(
    "bella bello stai sto ragazzi ragazze salve arrivederci buonasera "
    "buonanotte piacere ti mi tu io lui lei noi voi loro tutto cosa "
    "quando dove perché stato va vado posso fare un uno "
    "nella dal del al alla giorno sera sì".split()
)
LABELS = {"mt": "tgħid", "en": "says", "it": "dice"}
RATES = {"slow": "-20%", "normal": "+0%", "fast": "+20%"}
STRETCH = re.compile(r"([^\W\d_])\1{2,}", re.IGNORECASE)
GREETING = re.compile(r"(?:he+l+o+|hi+)")
ENGLISH_I = re.compile(r"([^\W\d_]\s+)l(?=\s+[^\W\d_])")


def common_language(word):
    """'en' or 'it' when the word is clearly common in that language (Zipf >= 4), else None."""
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return None
    english, italian = zipf_frequency(word, "en"), zipf_frequency(word, "it")
    if english >= 4.0 and english >= italian + 0.5:
        return "en"
    if italian >= 4.0 and italian >= english + 0.5:
        return "it"
    return None


def comment_language(text):
    scores = dict.fromkeys(LABELS, 0)
    unknown = []
    for match in WORD.finditer(text):
        word = key(match[0])
        variants = {word, STRETCH.sub(r"\1", word), STRETCH.sub(r"\1\1", word)}
        if re.search(r"[ċġħż]|għ|^(?:il-|ta'|f')", word) or variants & MT_WORDS:
            scores["mt"] += 1
        elif variants & EN_WORDS:
            scores["en"] += 1
        elif variants & IT_WORDS:
            scores["it"] += 1
        else:
            # The Maltese corpus also contains common English loans (hello, people), so ask
            # a general word-frequency list first: only clearly common EN/IT words count there.
            language = common_language(STRETCH.sub(r"\1", word))
            if language:
                scores[language] += 1
            else:
                unknown.append(word)
    if unknown and DB.is_file():
        try:
            with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)) as db:
                scores["mt"] += sum(
                    db.execute("SELECT 1 FROM words WHERE word=?", (word,)).fetchone()
                    is not None for word in unknown
                )
        except sqlite3.Error:
            pass
    highest = max(scores.values())
    winners = [language for language, score in scores.items() if score == highest]
    return winners[0] if len(winners) == 1 else "mt"


def readable_comment(text, settings):
    if not any(char.isalnum() for char in text):
        return False
    if settings["comment_skip_greetings"] and GREETING.fullmatch(
        " ".join(re.findall(r"[^\W_]+", text.casefold()))
    ):
        return False
    if settings["comment_mentions_only"]:
        name = " ".join(key(settings["user_name"]).split()).lstrip("@")
        return bool(name and re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, name.split()))
                                       + r"(?!\w)", key(text)))
    return True


def comment_speech(name, text, language, settings, corrections):
    text = STRETCH.sub(r"\1\1", text)
    if language == "mt":
        text = re.sub(r"\bsabihaa?\b", lambda match: match_case(match[0], "sabiħa"),
                      text, flags=re.IGNORECASE)
        text = prepare_maltese(text, corrections)
    elif language == "en":
        text = ENGLISH_I.sub(r"\1I", text)
    if name and settings["comment_author"]:
        text = f"{name} {LABELS[language]}: {text}"
    voice = settings["comment_language"]
    return text, voice if voice in ("mt", "en") else language, RATES.get(settings["comment_speed"], "+0%")
