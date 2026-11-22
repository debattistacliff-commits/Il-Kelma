"""Discover installed language packs.

Every pack is one folder with a language.json. `packs()` lists all of them; `chosen()` lists
only the ones the user picked for Buddy's flag, speech detection and comment reading."""
from functools import lru_cache
import json
from pathlib import Path

DEFAULT_CHOSEN = ("mt", "en", "it")
_chosen = list(DEFAULT_CHOSEN)


@lru_cache
def packs():
    entries = [(path.parent.name, json.loads(path.read_text(encoding="utf-8")))
               for path in Path(__file__).parent.glob("*/language.json")]
    return dict(sorted(entries, key=lambda entry: (entry[1]["order"], entry[0])))


def buddy_message(language, key):
    """Buddy's message and the language it is in. A new pack may translate only some
    messages; the rest are said in English, with the English voice."""
    pack = packs().get(language or "mt", {})
    if key in pack.get("buddy", {}):
        return pack["buddy"][key], language or "mt"
    return packs()["en"]["buddy"][key], "en"


def set_chosen(codes):
    """Maltese first, then the user's other languages in pack order."""
    wanted = set(codes or DEFAULT_CHOSEN)
    _chosen[:] = ["mt"] + [code for code in packs() if code in wanted and code != "mt"]


def chosen_codes():
    return tuple(_chosen)


def chosen():
    available = packs()
    return {code: available[code] for code in _chosen if code in available}


def flag_svg(code, size=42, hidden=False):
    """The pack's round flag, drawn on a 30 x 30 grid."""
    style = "border-radius:50%;display:" + ("none" if hidden else "block")
    return (f'<svg class="flag" data-code="{code.upper()}" width="{size}" height="{size}" '
            f'viewBox="0 0 30 30" style="{style}">{packs()[code].get("flag", "")}</svg>')
