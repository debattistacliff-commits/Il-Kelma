import json
import os
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
def windows_first_name():
    username = os.environ.get("USERNAME", "").strip()
    try:
        import win32api
        display_name = win32api.GetUserNameEx(3).strip()  # NameDisplay
    except (ImportError, OSError):
        display_name = ""
    return (display_name or username or "friend").split()[0]


DEFAULTS = {"language": "", "mic": None, "write_key": "F9", "read_key": "F10",
            "read_back": True, "voices": True, "dark": False, "buddy_position": None,
            "user_name": windows_first_name(), "buddy_name": "Kelma", "buddy_speaks": True,
            "learn_read_words": True, "engine": "whisper", "help_key": "F8",
            "comment_language": "auto", "comment_author": True, "comment_speed": "normal",
            "comment_skip_greetings": False, "comment_mentions_only": False,
            "my_languages": ["mt", "en", "it"]}


def load(name, fallback):
    try:
        return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def save(name, value):
    path = DATA / f"{name}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(path)
