"""Demonstrate basic (popularity) vs trending (popularity + recency) ranking.

Shows three states for the prefix "java":
  1. Basic ranking — purely by all-time count.
  2. Trending ranking right after a low-count query is searched repeatedly —
     it climbs above more popular but inactive queries.
  3. Trending ranking after time passes — the surge decays and the order
     reverts toward all-time popularity (anti "flash-in-the-pan").

A simulated clock (the `now` argument) lets us show decay without waiting.

Run from the backend/ directory:
    ./venv/Scripts/python.exe scripts/trending_demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ranking import enhanced_suggest  # noqa: E402
from app.recency import RecencyTracker  # noqa: E402
from app.trie import Trie  # noqa: E402

DATA = {
    "java": 1_000_000,
    "javascript": 800_000,
    "javadoc": 5_000,
    "javabean": 2_000,
    "javafx": 1_500,
}


def show(title, rows):
    print(title)
    for q, c in rows:
        print(f"    {q:12} count={c:>9,}")
    print()


def main() -> None:
    trie = Trie(k=50)
    for q, c in DATA.items():
        trie.insert(q, c)
    get_count = DATA.get

    rec = RecencyTracker(half_life=60.0)  # short half-life so decay is visible
    t0 = 1_000.0  # simulated start time (seconds)

    show("1) BASIC ranking (all-time count):", trie.suggest("java", 10))

    # A low-count query ('javadoc') is searched 8 times "now".
    for _ in range(8):
        rec.record("javadoc", now=t0)
    show(
        "2) TRENDING ranking just after 8 searches of 'javadoc':",
        enhanced_suggest(trie, rec, get_count, "java", limit=10, now=t0),
    )

    # 5 half-lives later (~5 minutes): the surge has decayed to ~3% of its peak.
    later = t0 + 5 * 60.0
    show(
        f"3) TRENDING ranking {int(later - t0)}s later (surge decayed):",
        enhanced_suggest(trie, rec, get_count, "java", limit=10, now=later),
    )

    print("Takeaway: recent activity lifts 'javadoc' to the top in state 2, but in")
    print("state 3 the decay returns the order toward all-time popularity.")


if __name__ == "__main__":
    main()
