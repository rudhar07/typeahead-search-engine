"""
trie.py — In-memory prefix index with precomputed Top-K suggestions per node.

WHY A TRIE (prefix tree):
  Typeahead matches a PREFIX on every keystroke. A trie walks to the node for a
  prefix in O(len(prefix)) — independent of N, the total number of queries.
  Scanning a list/table with `startswith` would be O(N) PER keystroke.

THE KEY OPTIMIZATION — store Top-K AT EACH NODE:
  Even after finding the prefix node, gathering the best 10 completions could
  mean walking a huge subtree (think prefix "a"). So at every node we keep the
  Top-K (default 10) completions for that prefix, already sorted by count.
  Then a suggestion lookup is: walk to the node -> return its list. Trivial.

  This is THE classic system-design trade: do extra work on WRITE (insert) so
  that READS (far more frequent, latency-sensitive) become cheap. Reads run on
  every keystroke; inserts happen on data load and on (batched) count updates.

COMPLEXITY:
  insert(query):  O(len(query) * k)   — touch each node on the path, merge into its top-k
  suggest(prefix): O(len(prefix) + k) — walk to node, copy its k-sized list
  Neither depends on N. That's the whole point.
"""
from __future__ import annotations

from typing import List, Tuple


class TrieNode:
    # __slots__ avoids a per-node __dict__, cutting memory noticeably when we
    # have hundreds of thousands of nodes.
    __slots__ = ("children", "top")

    def __init__(self) -> None:
        self.children: dict[str, "TrieNode"] = {}
        # `top` is kept sorted DESCENDING by count, length <= k.
        # We store (count, query) so the natural sort key is the count.
        self.top: List[Tuple[int, str]] = []


class Trie:
    def __init__(self, k: int = 10) -> None:
        self.root = TrieNode()
        self.k = k

    def insert(self, query: str, count: int) -> None:
        """Add/refresh a query with its count along its whole prefix path."""
        node = self.root
        self._merge_top(node, count, query)  # root represents the empty prefix
        for ch in query:
            nxt = node.children.get(ch)
            if nxt is None:
                nxt = TrieNode()
                node.children[ch] = nxt
            node = nxt
            self._merge_top(node, count, query)

    def _merge_top(self, node: TrieNode, count: int, query: str) -> None:
        """Merge (count, query) into node.top: keep sorted by count desc, trim to k.

        We build a NEW list and assign it in one step rather than mutating in
        place. That makes the update atomic for concurrent readers: a thread
        calling suggest() during a write sees either the old or the new list,
        never a half-updated one — so no lock is needed on the hot read path.
        Any existing entry for the same query is dropped first, so an updated
        count replaces it instead of duplicating.
        """
        merged = [(c, q) for (c, q) in node.top if q != query]
        merged.append((count, query))
        # Sort by count desc, tie-break by query for deterministic ordering.
        merged.sort(key=lambda t: (-t[0], t[1]))
        node.top = merged[: self.k]

    def suggest(self, prefix: str, limit: int = 10) -> List[Tuple[str, int]]:
        """Return up to `limit` (query, count) pairs for `prefix`, best first.

        Returns [] when the prefix matches nothing — the caller turns that into
        a graceful empty response (a functional requirement).
        """
        node = self.root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return []  # no query starts with this prefix
        # node.top is (count, query); flip to (query, count) for the API shape.
        return [(q, c) for (c, q) in node.top[:limit]]
