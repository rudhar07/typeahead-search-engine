"""Recency tracking for trending-aware ranking.

Each query carries an exponentially time-decayed weight. Recording a search adds
1.0 to the query's weight; the weight decays continuously with a configurable
half-life. This makes a brief spike fade on its own, so it cannot dominate the
rankings permanently.

The decay is computed lazily (only when a query is read or updated), so there is
no background thread to manage. All operations are thread-safe.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Dict, List, Optional, Tuple

DEFAULT_HALF_LIFE = 600.0    # seconds; a query's recency weight halves every 10 min
DEFAULT_CAPACITY = 10_000    # max distinct queries tracked (bounds memory)
PRUNE_THRESHOLD = 1e-3       # ignore/drop weights that have decayed below this


class RecencyTracker:
    def __init__(self, half_life: float = DEFAULT_HALF_LIFE, capacity: int = DEFAULT_CAPACITY) -> None:
        self.half_life = half_life
        self.capacity = capacity
        self._lambda = math.log(2.0) / half_life          # decay rate
        self._data: Dict[str, Tuple[float, float]] = {}    # query -> (weight, last_update)
        self._lock = threading.Lock()

    @staticmethod
    def now() -> float:
        # Monotonic clock: immune to wall-clock/NTP adjustments, which would
        # otherwise corrupt the decay maths.
        return time.monotonic()

    def _decayed(self, weight: float, last: float, now: float) -> float:
        return weight * math.exp(-self._lambda * (now - last))

    def record(self, query: str, now: Optional[float] = None) -> None:
        """Register that `query` was just searched."""
        now = self.now() if now is None else now
        with self._lock:
            weight, last = self._data.get(query, (0.0, now))
            self._data[query] = (self._decayed(weight, last, now) + 1.0, now)
            if len(self._data) > self.capacity:
                self._prune_locked(now)

    def score(self, query: str, now: Optional[float] = None) -> float:
        now = self.now() if now is None else now
        with self._lock:
            entry = self._data.get(query)
            return self._decayed(entry[0], entry[1], now) if entry else 0.0

    def scores_for(self, queries, now: Optional[float] = None) -> Dict[str, float]:
        """Current recency scores for a set of queries (those present only)."""
        now = self.now() if now is None else now
        with self._lock:
            return {
                q: self._decayed(*self._data[q], now)
                for q in queries
                if q in self._data
            }

    def matching(self, prefix: str, now: Optional[float] = None) -> List[Tuple[str, float]]:
        """Recently-active queries that start with `prefix`, with current score.

        This is what lets a surging query be ranked even if it is not among the
        prefix's most popular all-time completions.
        """
        now = self.now() if now is None else now
        with self._lock:
            items = list(self._data.items())
        out = []
        for q, (weight, last) in items:
            if q.startswith(prefix):
                s = self._decayed(weight, last, now)
                if s > PRUNE_THRESHOLD:
                    out.append((q, s))
        return out

    def top(self, n: int = 10, now: Optional[float] = None) -> List[Tuple[str, float]]:
        """The current top `n` trending queries by recency score."""
        now = self.now() if now is None else now
        with self._lock:
            items = list(self._data.items())
        scored = [(q, self._decayed(w, last, now)) for q, (w, last) in items]
        scored = [t for t in scored if t[1] > PRUNE_THRESHOLD]
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:n]

    def _prune_locked(self, now: float) -> None:
        # Keep memory bounded: drop the lowest-scoring entries down to capacity.
        ranked = sorted(self._data.items(), key=lambda kv: self._decayed(kv[1][0], kv[1][1], now))
        for q, _ in ranked[: len(self._data) - self.capacity]:
            del self._data[q]
