"""Discover installed language packs."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache
def packs():
    entries = [(path.parent.name, json.loads(path.read_text(encoding="utf-8")))
               for path in Path(__file__).parent.glob("*/language.json")]
    return dict(sorted(entries, key=lambda entry: entry[1]["order"]))
