"""Recency-aware ("trending") ranking.

Basic ranking sorts purely by all-time count (handled by the trie). Enhanced
ranking blends all-time popularity with recent activity so a query that is
surging now can outrank a dormant all-time favourite, while the same query falls
back to its historical position once the activity fades.

Blended score for a candidate q:

    score(q) = log1p(all_time_count(q)) + weight * recency_score(q)

log1p compresses the very wide range of all-time counts (which span several
orders of magnitude), so the recency term can meaningfully reorder results
instead of being swamped by the raw count.
"""
from __future__ import annotations

import math
from typing import Callable, List, Optional, Tuple

from app.recency import RecencyTracker
from app.trie import Trie

DEFAULT_CANDIDATE_N = 50      # popular completions pulled from the trie before re-ranking
DEFAULT_RECENCY_WEIGHT = 3.0  # how strongly recent activity influences the order


def enhanced_suggest(
    trie: Trie,
    recency: RecencyTracker,
    get_count: Callable[[str], Optional[int]],
    prefix: str,
    limit: int = 10,
    candidate_n: int = DEFAULT_CANDIDATE_N,
    weight: float = DEFAULT_RECENCY_WEIGHT,
    now: Optional[float] = None,
) -> List[Tuple[str, int]]:
    """Return up to `limit` (query, count) pairs ordered by blended score.

    Candidates are drawn from two sources so a surging query is never missed:
      1. the prefix's most popular completions (trie top-N), and
      2. recently-active queries that start with the prefix (recency tracker).
    The returned counts are the all-time counts; only the ORDER reflects recency.
    """
    # 1) popular candidates from the trie (a wider pool than the final `limit`)
    counts = {q: c for q, c in trie.suggest(prefix, limit=candidate_n)}

    # 2) recently-active candidates matching the prefix (may not be in the pool above)
    for q, _ in recency.matching(prefix, now=now):
        if q not in counts:
            counts[q] = get_count(q) or 0

    recency_scores = recency.scores_for(counts.keys(), now=now)

    ranked = sorted(
        counts.items(),
        key=lambda item: (
            -(math.log1p(item[1]) + weight * recency_scores.get(item[0], 0.0)),
            item[0],  # deterministic tie-break
        ),
    )
    return [(q, c) for q, c in ranked[:limit]]
