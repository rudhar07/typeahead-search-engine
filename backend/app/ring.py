"""
ring.py — Consistent-hash ring (with virtual nodes).

WHY consistent hashing: we spread the suggestion cache across N logical nodes.
We need a stable rule for "which node owns prefix P?" such that ADDING or
REMOVING a node moves only ~1/N of keys — not almost all of them, which is what
plain `hash(key) % N` does (changing N remaps nearly every key -> cache-miss
storm).

HOW: place each node at many points ("virtual nodes") on a circular hash space
[0, 2**32). A key is owned by the first node point CLOCKWISE from the key's hash.
Lookup is a binary search (bisect) on the sorted node positions, with wraparound.

Standard library only (hashlib, bisect, threading) — fully explainable line by line.
"""
from __future__ import annotations

import bisect
import hashlib
import threading

RING_SPACE = 2 ** 32  # the ring is positions 0 .. 2**32-1, wrapping around


def ring_hash(s: str) -> int:
    """Map any string to a point on the ring, deterministically.

    We use SHA-1 and take the first 8 hex digits (= 32 bits). WHY not Python's
    built-in hash(): it is salted per process (PYTHONHASHSEED), so it changes on
    every restart — useless for a ring you must reason about and DEMONSTRATE.
    SHA-1 is deterministic and uniformly distributed. We use it for spread, NOT
    security, so it is a fine choice (md5 would work equally well). 32 bits is
    plenty: a few hundred points in a 4-billion-slot space => collisions are
    astronomically rare (and we handle them anyway).
    """
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:8], 16)


class HashRing:
    def __init__(self, nodes: list[str] | None = None, vnodes: int = 150) -> None:
        self.vnodes = vnodes
        self._ring: dict[int, str] = {}   # ring position -> node name
        self._positions: list[int] = []   # sorted ring positions (the bisect array)
        self._nodes: set[str] = set()
        # RLock: add/remove and lookups can be called from different threads
        # (uvicorn serves requests on a threadpool).
        self._lock = threading.RLock()
        for node in nodes or []:
            self.add_node(node)

    # ------------------------------------------------------------------ mutate
    @staticmethod
    def _vnode_label(node: str, i: int) -> str:
        # The exact string we hash for replica i of a node. Deterministic, so
        # the ring is identical across restarts.
        return f"{node}#{i}"

    def add_node(self, node: str) -> None:
        with self._lock:
            if node in self._nodes:
                return
            self._nodes.add(node)
            for i in range(self.vnodes):
                pos = ring_hash(self._vnode_label(node, i))
                # Collision handling: in a 2**32 space with a few hundred points
                # this is vanishingly unlikely, but if two labels land on the same
                # slot we probe to the next free one so no node silently loses a
                # replica (which would skew load).
                while pos in self._ring:
                    pos = (pos + 1) % RING_SPACE
                self._ring[pos] = node
            self._rebuild()

    def remove_node(self, node: str) -> None:
        with self._lock:
            if node not in self._nodes:
                return
            self._nodes.discard(node)
            self._ring = {p: n for p, n in self._ring.items() if n != node}
            self._rebuild()

    def _rebuild(self) -> None:
        # Assign a NEW sorted list (don't mutate in place) so a concurrent reader
        # holding the old reference always sees a consistent snapshot.
        self._positions = sorted(self._ring)

    # ------------------------------------------------------------------ lookup
    def get_node(self, key: str) -> str | None:
        """Return the node that owns `key`, or None if the ring is empty."""
        with self._lock:
            if not self._positions:
                return None
            h = ring_hash(key)
            # bisect_right -> index of the first position STRICTLY greater than h,
            # i.e. the next point clockwise. (If h lands exactly on a vnode, we
            # move to the next one — a fixed, documented tie rule.)
            idx = bisect.bisect_right(self._positions, h)
            if idx == len(self._positions):
                idx = 0  # WRAPAROUND: past the last point -> first point clockwise
            return self._ring[self._positions[idx]]

    def positions_for(self, key: str) -> tuple[int, int, str | None]:
        """For /cache/debug: (key_hash, owning_vnode_position, owner_node)."""
        with self._lock:
            h = ring_hash(key)
            if not self._positions:
                return (h, -1, None)
            idx = bisect.bisect_right(self._positions, h)
            if idx == len(self._positions):
                idx = 0
            owner_pos = self._positions[idx]
            return (h, owner_pos, self._ring[owner_pos])

    @property
    def nodes(self) -> list[str]:
        with self._lock:
            return sorted(self._nodes)

    def distribution(self, keys: list[str]) -> dict[str, int]:
        """Route many keys and count how many land on each node. Used to show
        that virtual nodes give a roughly even load split."""
        with self._lock:
            nodes = list(self._nodes)  # snapshot so a concurrent add/remove can't
                                       # raise "set changed size during iteration"
        counts: dict[str, int] = {n: 0 for n in nodes}
        for k in keys:
            owner = self.get_node(k)
            if owner is not None:
                counts[owner] = counts.get(owner, 0) + 1
        return counts
