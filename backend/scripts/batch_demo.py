"""Demonstrate how batch writes reduce the number of database writes.

Simulates many search submissions drawn from a small set of popular queries (so
there are many duplicates) and compares:
  - naive:   one DB write per search
  - batched: one DB write per flush, with duplicate queries aggregated

The flusher thread is not started here; flushes are driven deterministically when
the buffer reaches its size threshold, so the numbers are reproducible.

Run from the backend/ directory:
    ./venv/Scripts/python.exe scripts/batch_demo.py
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.batch import WriteBuffer  # noqa: E402

N = 1000
QUERIES = ["iphone", "ipad", "java", "python", "search", "news", "weather", "maps"]
BATCH_SIZE = 50


def main() -> None:
    random.seed(42)
    searches = [random.choice(QUERIES) for _ in range(N)]

    db_writes = 0   # number of flush transactions
    rows_written = 0  # number of row upserts across all flushes

    def fake_flush(batch: dict) -> None:
        nonlocal db_writes, rows_written
        db_writes += 1
        rows_written += len(batch)

    buf = WriteBuffer(fake_flush, batch_size=BATCH_SIZE, flush_interval=9999)
    for q in searches:
        buf.add(q)
        if buf.pending_count() >= BATCH_SIZE:
            buf.flush()
    buf.flush()  # final flush of the remainder

    print(f"Searches submitted:          {N}")
    print(f"Distinct queries:            {len(set(searches))}")
    print()
    print(f"Naive DB writes (1/search):  {N}")
    print(f"Batched DB transactions:     {db_writes}   ({N / db_writes:.0f}x fewer)")
    print(f"Total row upserts (batched): {rows_written}   (duplicates aggregated per flush)")
    print()
    print("Takeaway: batching cuts transactions ~{:.0f}x; aggregation further".format(N / db_writes))
    print(f"shrinks total row writes from {N} to {rows_written}.")


if __name__ == "__main__":
    main()
