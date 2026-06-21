"""
cache.py — Distributed suggestion cache.

N logical cache nodes (shards), each a bounded LRU store with per-entry TTL,
addressed by the consistent-hash ring in ring.py. The read path uses the
CACHE-ASIDE pattern: the application checks the cache first and, on a miss,
computes the answer (from the trie) and stores it here.

Layering (top = fast, bottom = durable):
    cache (this file)  ->  trie (in-memory index)  ->  SQLite store (truth)

Standard library only (threading, time, collections.OrderedDict).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

from app.ring import HashRing

DEFAULT_VNODES = 150       # virtual nodes per physical node (smooths load)
DEFAULT_TTL = 30.0         # seconds; bounds staleness even if invalidation misses
DEFAULT_CAPACITY = 2048    # max entries per node; LRU-evict beyond this
MAX_PREFIX_LEN = 50        # cap invalidation fan-out for pathologically long queries

# A cached value is the list of (query, count) pairs that trie.suggest() returns.
Suggestions = List[Tuple[str, int]]


class CacheNode:
    """One logical cache shard: bounded LRU + per-entry TTL, thread-safe."""

    def __init__(self, name: str, capacity: int = DEFAULT_CAPACITY, ttl: float = DEFAULT_TTL):
        self.name = name
        self.capacity = capacity
        self.ttl = ttl
        # prefix -> (suggestions, expires_at)  where expires_at is a monotonic time
        self._data: "OrderedDict[str, Tuple[Suggestions, float]]" = OrderedDict()
        self._lock = threading.Lock()  # one lock per node => shards don't contend
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, prefix: str) -> Optional[Suggestions]:
        """Return cached suggestions, or None on a MISS (absent OR expired).

        Important: a stored EMPTY list [] (negative cache for a no-match prefix)
        is a valid HIT and is returned as []. None means truly absent/expired.
        """
        with self._lock:
            entry = self._data.get(prefix)
            if entry is None:
                self.misses += 1
                return None
            suggestions, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._data[prefix]        # lazy expiry: evict on read
                self.misses += 1
                return None
            self._data.move_to_end(prefix)    # LRU: mark most-recently-used
            self.hits += 1
            return list(suggestions)          # defensive copy (caller can't mutate ours)

    def set(self, prefix: str, suggestions: Suggestions) -> None:
        with self._lock:
            self._data[prefix] = (list(suggestions), time.monotonic() + self.ttl)
            self._data.move_to_end(prefix)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)  # evict least-recently-used (front)
                self.evictions += 1

    def invalidate(self, prefix: str) -> bool:
        with self._lock:
            return self._data.pop(prefix, None) is not None

    def peek(self, prefix: str) -> Tuple[bool, Optional[Suggestions], Optional[float]]:
        """READ-ONLY inspection for /cache/debug.

        Does NOT touch LRU order or hit/miss counters — otherwise observing the
        cache would change it and corrupt the reported hit rate (a Heisenbug).
        Returns (is_live_hit, suggestions_or_None, ttl_remaining_or_None).
        """
        with self._lock:
            entry = self._data.get(prefix)
            if entry is None:
                return (False, None, None)
            suggestions, expires_at = entry
            remaining = expires_at - time.monotonic()
            if remaining <= 0:
                return (False, None, None)
            return (True, list(suggestions), remaining)

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "size": len(self._data),
                "capacity": self.capacity,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
            }


class DistributedCache:
    """N CacheNodes tied together by one consistent-hash ring."""

    def __init__(self, node_names, vnodes=DEFAULT_VNODES, capacity=DEFAULT_CAPACITY, ttl=DEFAULT_TTL):
        node_names = list(node_names)
        self.ring = HashRing(node_names, vnodes=vnodes)
        self.nodes = {name: CacheNode(name, capacity, ttl) for name in node_names}
        self.ttl = ttl
        self.capacity = capacity

    def _node_for(self, prefix: str) -> Optional[CacheNode]:
        owner = self.ring.get_node(prefix)
        return self.nodes.get(owner) if owner else None

    def get_routed(self, prefix: str) -> Tuple[Optional[str], Optional[Suggestions]]:
        """Return (owner_node_name, cached_value_or_None). The owner name is handy
        for logging which node served (or missed) the request."""
        owner = self.ring.get_node(prefix)
        if owner is None:
            return (None, None)
        # Use .get() (not self.nodes[owner]): if `owner` is removed between the
        # ring lookup and here (concurrent membership change), degrade to a clean
        # miss instead of raising KeyError -> 500.
        node = self.nodes.get(owner)
        return (owner, node.get(prefix) if node else None)

    def get(self, prefix: str) -> Optional[Suggestions]:
        node = self._node_for(prefix)
        return node.get(prefix) if node else None

    def set(self, prefix: str, suggestions: Suggestions) -> None:
        # Cap the cache key space to MAX_PREFIX_LEN so it matches the invalidation
        # key space (invalidate_prefixes also stops at MAX_PREFIX_LEN). Without
        # this, a prefix longer than the cap could be cached but never invalidated
        # on a write -> stale until TTL. Over-long prefixes simply aren't cached.
        if len(prefix) > MAX_PREFIX_LEN:
            return
        node = self._node_for(prefix)
        if node:
            node.set(prefix, suggestions)

    def invalidate(self, prefix: str) -> bool:
        node = self._node_for(prefix)
        return node.invalidate(prefix) if node else False

    def invalidate_prefixes(self, query: str) -> int:
        """Invalidate every cached prefix whose top-K could change because
        `query` was just searched.

        Those are EXACTLY the prefixes of `query` (for "java": j, ja, jav, java),
        because a prefix P's suggestions can only change if some query starting
        with P changed count — and the only one that did is `query`, which starts
        with P iff P is a prefix of `query`. Provably complete and minimal.

        Each prefix is routed INDEPENDENTLY through the ring — different prefixes
        of one query can live on different nodes, so we must not assume co-location.
        """
        count = 0
        upper = min(len(query), MAX_PREFIX_LEN)
        for i in range(1, upper + 1):
            if self.invalidate(query[:i]):
                count += 1
        return count

    def debug(self, prefix: str) -> dict:
        """Build the /cache/debug payload: which node owns the prefix and whether
        it is currently a hit or a miss. Read-only (uses peek)."""
        key_hash, owner_pos, owner = self.ring.positions_for(prefix)
        present, cached, ttl_remaining = (False, None, None)
        node = self.nodes.get(owner) if owner else None
        if node:
            present, cached_pairs, ttl_remaining = node.peek(prefix)
            if present:
                cached = [{"query": q, "count": c} for q, c in cached_pairs]
        return {
            "prefix": prefix,
            "owner_node": owner,
            "status": "hit" if present else "miss",
            "cached": present,
            "ttl_remaining_seconds": round(ttl_remaining, 2) if ttl_remaining else None,
            "cached_suggestions": cached,
            "ring": {
                "key_hash": key_hash,
                "owner_vnode_position": owner_pos,
                "nodes": self.ring.nodes,
                "vnodes_per_node": self.ring.vnodes,
                "total_points": len(self.ring.nodes) * self.ring.vnodes,
            },
        }

    def metrics(self) -> dict:
        # Snapshot to a list first: iterating self.nodes.values() directly could
        # raise "dictionary changed size during iteration" if a node is added/
        # removed concurrently (this runs on uvicorn's threadpool).
        per_node = [n.stats() for n in list(self.nodes.values())]
        hits = sum(s["hits"] for s in per_node)
        misses = sum(s["misses"] for s in per_node)
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
            "total_cached_keys": sum(s["size"] for s in per_node),
            "per_node": per_node,
        }

    # --- live membership changes (for the consistent-hashing demo) -----------
    def add_node(self, name: str) -> None:
        # Create the CacheNode BEFORE the ring routes to it, so a concurrent
        # reader never sees a ring entry without a backing node.
        self.nodes.setdefault(name, CacheNode(name, self.capacity, self.ttl))
        self.ring.add_node(name)

    def remove_node(self, name: str) -> None:
        # Remove from the ring first (stop routing to it), then drop the node.
        self.ring.remove_node(name)
        self.nodes.pop(name, None)
