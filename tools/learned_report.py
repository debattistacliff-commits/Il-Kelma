"""Print aggregate learned-word totals without opening the database for writing."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3


DB = Path(__file__).resolve().parents[1] / "data" / "learned_words.sqlite"


def counts(path=DB):
    totals = {"vocabulary": [0, 0], "spelling_patterns": [0, 0], "new_words": [0, 0]}
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if table in {"vocabulary", "spelling_patterns"}:
                    label = table
                elif re.fullmatch(r"pending_[0-9]+", table):
                    label = "new_words"
                else:
                    continue
                rows, sightings = db.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(count), 0) FROM {table}").fetchone()
                totals[label][0] += rows
                totals[label][1] += sightings
    return totals


if __name__ == "__main__":
    for table, (rows, sightings) in counts().items():
        print(f"{table}: {rows} entries, {sightings} sightings")
