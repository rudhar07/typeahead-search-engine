"""
load_data.py — Read the raw dataset and load it into SQLite.

DATASET: Peter Norvig's `count_1w.txt` — the 1/3 million most frequent English
words from the Google Web Trillion Word Corpus. Each line is:
    <word>\t<count>
This already matches the assignment's expected (query, count) shape, and the
file is pre-sorted by count DESC, so the first N lines are the N most popular
words — a natural, high-quality subset.

WHY default to 100,000 words:
  The assignment requires a MINIMUM of 100,000 queries. Loading the top 100k by
  count meets that exactly while keeping trie build fast and memory modest.
  Pass --limit 0 to load all 333,333 ("larger datasets are encouraged").

Run from the backend/ directory:
    ./venv/Scripts/python.exe scripts/load_data.py             # top 100k
    ./venv/Scripts/python.exe scripts/load_data.py --limit 0   # all 333,333
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Make the sibling `app` package importable when running this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.store import Store  # noqa: E402

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "count_1w.txt"
)
DEFAULT_LIMIT = 100_000


def parse_rows(path: str, limit: int) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            parts = line.strip().split("\t")
            if len(parts) != 2:
                continue
            word, count = parts[0].strip().lower(), parts[1].strip()
            if not word or not count.isdigit():
                continue  # skip malformed lines gracefully
            rows.append((word, int(count)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Load dataset into SQLite")
    ap.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help="max words to load (0 = all 333,333)",
    )
    args = ap.parse_args()

    print(f"Reading {DATA_FILE} (limit={args.limit or 'ALL'}) ...")
    t0 = time.time()
    rows = parse_rows(DATA_FILE, args.limit)
    print(f"Parsed {len(rows):,} rows in {time.time() - t0:.2f}s")

    store = Store()
    total = store.bulk_load(rows)
    print(f"Loaded into SQLite. DB now holds {total:,} queries.")

    # Sanity check a few prefixes/words so we can eyeball that it worked.
    for w in ("the", "iphone", "java", "python", "search"):
        print(f"  count[{w!r}] = {store.get_count(w)}")


if __name__ == "__main__":
    main()
