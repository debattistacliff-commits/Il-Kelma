import re
import unicodedata


WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)
FOLD = str.maketrans("ċġħżћ’", "cghzh'")
MT_GRAMMAR = set("jiena inti qed biex fejn kif nitkellem nibda ghaliex għaliex kienet nixtieq".split())
MT_MARKERS = MT_GRAMMAR | set("tiegħi tieghi miegħek miehek ħafna hafna għax ghax "
                             "għandek ghandek għandi ghandi bonġu bongu ħabib habib "
                             "tajba qiegħed qieghed xogħol xoghol".split())
EN_MARKERS = set("the this that you your we please is are was have would can and with "
                 "good morning friend hello how today my much there i to of for on from "
                 "but or not do did will he she they take home yesterday".split())
IT_MARKERS = set("lo gli delle della che sono sei ciao buongiorno come oggi molto "
                 "grazie amico per questo questa una vorrei bene mio mia non è ho hai "
                 "siamo tutti anche nel degli con".split())


def key(word):
    return unicodedata.normalize("NFC", word).lower().replace("’", "'")


def folded(word):
    return key(word).translate(FOLD)


def macocu_fold(word):
    return folded(word).replace("à", "a")


def detect_language(text, default="en"):
    words = {key(match[0]) for match in WORD.finditer(unicodedata.normalize("NFC", text))}
    maltese = len(words & MT_MARKERS)
    marked = any(set(word) & set("ċġħżћ") for word in words)
    other = {"en": len(words & EN_MARKERS), "it": len(words & IT_MARKERS)}
    has_maltese_cue = marked or maltese >= 2 or words & MT_GRAMMAR or words & {"hafna", "ħafna"} or maltese == len(words) == 1
    if has_maltese_cue and not any(other.values()):
        return "mt"
    if max(other.values()) > 0:
        return max(other, key=other.get)
    return default
