"""Batched, aggregated write buffer for search-count updates.

Instead of one database write per /search, submissions are accumulated in memory
with duplicates aggregated, and a background thread flushes them to the store in
batches — either every `flush_interval` seconds or once `batch_size` searches
have been buffered, whichever comes first.

This cuts the number of database writes dramatically (one transaction per flush
instead of one per search, and duplicate queries collapse into a single row
update). The cost is a small window in which a hard crash would lose the
not-yet-flushed counts — an acceptable, eventual-consistency trade-off for
popularity counters. A graceful shutdown performs a final flush.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

DEFAULT_BATCH_SIZE = 50        # flush once this many searches are buffered
DEFAULT_FLUSH_INTERVAL = 2.0   # ...or at least this often (seconds)


class WriteBuffer:
    def __init__(
        self,
        flush_fn: Callable[[Dict[str, int]], None],
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    ) -> None:
        self._flush_fn = flush_fn           # called with the aggregated {query: delta}
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._pending: Dict[str, int] = {}  # query -> buffered increment
        self._pending_total = 0             # sum of buffered increments
        self._lock = threading.Lock()
        self._wake = threading.Event()      # signals "flush now" (size threshold hit)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # metrics for the performance report
        self.searches_received = 0
        self.flushes = 0
        self.rows_written = 0

    def add(self, query: str, by: int = 1) -> None:
        """Buffer one search. O(1); never blocks on the database."""
        with self._lock:
            self._pending[query] = self._pending.get(query, 0) + by
            self._pending_total += by
            self.searches_received += by
            over_threshold = self._pending_total >= self.batch_size
        if over_threshold:
            self._wake.set()  # ask the flusher to flush now (don't block the caller)

    def flush(self) -> int:
        """Write the buffered increments in one batch. Returns rows written."""
        with self._lock:
            if not self._pending:
                return 0
            batch = self._pending           # take the current buffer...
            self._pending = {}              # ...and reset it so adds keep flowing
            self._pending_total = 0
        self._flush_fn(batch)               # the actual DB write (outside the lock)
        with self._lock:
            self.flushes += 1
            self.rows_written += len(batch)
        return len(batch)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="write-flusher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # Wake every flush_interval, or early when a size-triggered flush is signalled.
        while True:
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.flush()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()  # unblock the flusher so it exits promptly
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()      # final flush: a graceful shutdown loses nothing

    def pending_count(self) -> int:
        with self._lock:
            return self._pending_total

    def stats(self) -> dict:
        with self._lock:
            return {
                "searches_received": self.searches_received,
                "flushes": self.flushes,
                "rows_written": self.rows_written,
                "pending": self._pending_total,
                "pending_distinct": len(self._pending),
                "batch_size": self.batch_size,
                "flush_interval": self.flush_interval,
            }
