"""Train exact word replacements and score the official SPELL-MT test splits."""

import argparse
from collections import Counter, defaultdict
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata
from unittest.mock import patch

from maltese_fix import DATA, fix
from maltese_text import WORD, key


SOURCE = DATA.parent / "sources" / "spell-mt"
DATASETS = ("kotbafilbut_1", "MV2050", "type_A_balanced")
TOKENS = re.compile(f"{WORD.pattern}|[^\\s]")


def rows(dataset, split):
    with (SOURCE / f"{dataset}.{split}.csv").open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            yield tuple(unicodedata.normalize("NFC", row[field])
                        for field in ("src_sent", "tgt_sent"))


def alignment(before, after):
    left, right = TOKENS.findall(before), TOKENS.findall(after)
    for op, start, end, first, last in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        yield op, start, end, tuple(left[start:end]), tuple(right[first:last])


def train():
    changes = defaultdict(Counter)
    unchanged = Counter()
    for dataset in DATASETS:
        for before, after in set(rows(dataset, "train")):
            for op, _, _, old, new in alignment(before, after):
                if op == "equal":
                    unchanged.update(map(key, old))
                if op == "replace" and len(old) == len(new) == 1:
                    source, target = key(old[0]), key(new[0])
                    if source != target and WORD.fullmatch(source) and WORD.fullmatch(target):
                        changes[source][target] += 1
    pairs = {}
    for source, targets in sorted(changes.items()):
        target, count = targets.most_common(1)[0]
        if count >= 2 and count * 5 >= 4 * (targets.total() + unchanged[source]):
            pairs[source] = target
    (DATA / "spell_mt_pairs.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Learned {len(pairs)} word replacements.")


def edits(before, after):
    return {(start, end, new) for op, start, end, _, new in alignment(before, after)
            if op != "equal"}


def benchmark():
    print("Dataset              Rows   Precision     Recall    Input exact   Output exact    Hits/Attempts/Gold")
    total = Counter()
    for dataset in DATASETS:
        stats = Counter()
        for before, gold in rows(dataset, "test"):
            after = fix(before, lang="mt")
            expected, predicted = edits(before, gold), edits(before, after)
            stats.update(rows=1, input_exact=int(before == gold), output_exact=int(after == gold),
                         hits=len(expected & predicted), attempts=len(predicted), gold=len(expected))
        total.update(stats)
        show(dataset, stats)
    show("All splits", total)


def show(dataset, stats):
    n = stats["rows"]
    precision = f"{stats['hits'] / stats['attempts']:10.2%}" if stats["attempts"] else "       n/a"
    print(f"{dataset:19} {n:5} {precision} {stats['hits'] / stats['gold']:10.2%} "
          f"{stats['input_exact'] / n:13.2%} {stats['output_exact'] / n:14.2%} "
          f"{stats['hits']}/{stats['attempts']}/{stats['gold']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("train", "benchmark", "baseline"))
    args = parser.parse_args()
    if args.command == "baseline":
        with patch("maltese_fix.spelling_resources", return_value=({}, set())):
            benchmark()
    elif args.command == "train":
        train()
    else:
        benchmark()
