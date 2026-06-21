"""
ch_demo.py — Proof that consistent hashing behaves as claimed.

Demonstrates two properties the assignment asks us to show:
  1. EVEN LOAD: with virtual nodes, keys split roughly evenly across nodes.
  2. ~1/N KEY MOVEMENT: adding/removing a node moves only ~1/N of keys —
     and contrasts that with naive `hash % N`, which moves almost everything.

Run from the backend/ directory:
    ./venv/Scripts/python.exe scripts/ch_demo.py
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ring import HashRing  # noqa: E402

K = 100_000
KEYS = [f"prefix-{i}" for i in range(K)]  # deterministic synthetic keys


def main() -> None:
    # --- 1) distribution across 3 nodes ---
    ring = HashRing([f"cache-{i}" for i in range(3)], vnodes=150)
    dist = ring.distribution(KEYS)
    print(f"1) Distribution of {K:,} keys across {len(dist)} nodes (vnodes=150):")
    for node, c in sorted(dist.items()):
        print(f"     {node}: {c:>7,}  ({100 * c / K:5.1f}%)   (ideal {100/len(dist):.1f}%)")

    # --- 2) add a 4th node: how many keys move? ---
    before = {k: ring.get_node(k) for k in KEYS}
    ring.add_node("cache-3")
    after = {k: ring.get_node(k) for k in KEYS}
    moved = sum(1 for k in KEYS if before[k] != after[k])
    print(f"\n2) Added cache-3 (3 -> 4 nodes):")
    print(f"     keys moved: {moved:,}/{K:,} = {100 * moved / K:.1f}%   (ideal ~1/4 = 25%)")

    # --- 3) remove it again: how many keys move? ---
    before2 = {k: ring.get_node(k) for k in KEYS}
    ring.remove_node("cache-3")
    after2 = {k: ring.get_node(k) for k in KEYS}
    moved2 = sum(1 for k in KEYS if before2[k] != after2[k])
    print(f"\n3) Removed cache-3 (4 -> 3 nodes):")
    print(f"     keys moved: {moved2:,}/{K:,} = {100 * moved2 / K:.1f}%   (ideal ~1/4 = 25%)")

    # --- 4) contrast with naive hash % N ---
    def hmod(key: str, n: int) -> int:
        h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)
        return h % n

    moved_naive = sum(1 for k in KEYS if hmod(k, 3) != hmod(k, 4))
    print(f"\n4) Contrast — naive `hash % N` (3 -> 4 nodes):")
    print(f"     keys moved: {100 * moved_naive / K:.1f}%   (almost ALL keys remap -> cache-miss storm)")


if __name__ == "__main__":
    main()
