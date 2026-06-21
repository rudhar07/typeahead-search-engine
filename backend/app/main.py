"""
main.py — FastAPI application.

MILESTONE 1 scope: expose GET /suggest, backed by the in-memory Trie which is
built at startup from the SQLite store (the durable source of truth).

Later milestones add: POST /search, the distributed cache + /cache/debug,
trending, and batch writes. We grow this file one milestone at a time.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.batch import WriteBuffer
from app.cache import ENHANCED_NS, ENHANCED_TTL, DistributedCache
from app.ranking import enhanced_suggest
from app.recency import RecencyTracker
from app.store import Store
from app.trie import Trie

logger = logging.getLogger("typeahead")
logger.setLevel(logging.INFO)
# Attach our own handler so routing logs print regardless of uvicorn's config.
# (A bare logger with no handler only emits WARNING+ via the last-resort handler,
# so our INFO HIT/MISS lines would silently vanish.) propagate=False avoids dupes.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [typeahead] %(message)s"))
    logger.addHandler(_handler)
logger.propagate = False

# Module-level singletons shared by all requests. The cache has 3 logical nodes
# addressed by a consistent-hash ring; the recency tracker feeds trending ranking.
# The trie keeps top-50 per node: the first 10 serve basic ranking, and the wider
# pool is the candidate set the trending path re-ranks by recency.
store = Store()
trie = Trie(k=50)
cache = DistributedCache([f"cache-{i}" for i in range(3)])
recency = RecencyTracker()


def _fmt(pairs):
    """Turn [(query, count), ...] into the API's [{"query":..,"count":..}, ...]."""
    return [{"query": q, "count": c} for q, c in pairs]


def _flush_batch(increments: dict) -> None:
    """Flush callback: write a batch of aggregated increments in one DB
    transaction, then refresh the read index and drop stale cache entries for
    the affected queries so basic suggestions catch up."""
    store.apply_batch(increments)
    for query in increments:
        trie.insert(query, store.get_count(query) or 0)
        cache.invalidate_prefixes(query)


# Buffers /search increments and flushes them to SQLite in batches.
buffer = WriteBuffer(_flush_batch)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup: build the trie (our fast read index) from SQLite ----------
    # WHY at startup: the trie is an in-memory *materialized view* of the store.
    # SQLite is durable; the trie is rebuilt from it whenever the server starts.
    n = 0
    for q, c in store.iter_all():
        trie.insert(q, c)
        n += 1
    print(f"[startup] trie built from {n:,} queries")
    buffer.start()  # begin the background batch flusher
    yield
    # ---- shutdown: flush any buffered search counts before exiting -----------
    buffer.stop()


app = FastAPI(title="Search Typeahead", lifespan=lifespan)


@app.get("/suggest")
def suggest(
    q: str = Query(default="", description="prefix the user has typed"),
    mode: str = Query(default="basic", description="ranking mode: 'basic' or 'trending'"),
):
    """Typeahead suggestions for a prefix, served via CACHE-ASIDE.

    Two ranking modes share this one endpoint:
      - basic    -> sort by all-time count (the trie's precomputed top-K)
      - trending -> blend all-time count with recent activity (recency-aware)

    Flow: normalize prefix -> route to the owning cache node -> HIT returns the
    cached list; MISS computes the ranking, caches it, and returns it. The two
    modes use separate cache keys, and trending entries use a short TTL because
    their scores drift continuously.
    """
    prefix = q.strip().lower()
    if not prefix:
        return {"prefix": prefix, "mode": mode, "suggestions": []}

    trending = mode == "trending"
    cache_key = (ENHANCED_NS + prefix) if trending else prefix
    ttl = ENHANCED_TTL if trending else None

    owner, cached = cache.get_routed(cache_key)
    if cached is not None:  # [] is a valid (negative-cache) hit, hence "is not None"
        logger.info("suggest prefix=%r mode=%s node=%s HIT", prefix, mode, owner)
        return {"prefix": prefix, "mode": mode, "suggestions": _fmt(cached)}

    # Cache miss: compute the ranking, then populate the owning node.
    if trending:
        results = enhanced_suggest(trie, recency, store.get_count, prefix, limit=10)
    else:
        results = trie.suggest(prefix, limit=10)
    cache.set(cache_key, results, ttl=ttl)
    logger.info("suggest prefix=%r mode=%s node=%s MISS", prefix, mode, owner)
    return {"prefix": prefix, "mode": mode, "suggestions": _fmt(results)}


class SearchIn(BaseModel):
    q: str


@app.post("/search")
def search(body: SearchIn):
    """Record a submitted search and return the dummy response.

    Functional requirements handled here:
      - returns {"message": "Searched"}        (the required dummy response)
      - existing query -> count increases       \
      - new query      -> inserted with count   } via store.increment (UPSERT)
      - update reflected in suggestions          -> we refresh the trie path

    NOTE (milestone honesty): this writes to the DB synchronously, one write per
    search. The batch-writes milestone replaces this with a buffer that
    aggregates + flushes, cutting DB writes. We keep this version as the baseline
    to measure the improvement against.
    """
    query = body.q.strip().lower()
    if not query:
        return {"message": "Searched", "query": ""}
    # Buffer the count update instead of writing to the DB synchronously: the
    # write buffer aggregates duplicates and a background thread flushes batches.
    buffer.add(query)
    # Recency is in-memory, so update it immediately — trending reflects the
    # search at once; basic suggestions catch up at the next batch flush.
    recency.record(query)
    logger.info("search q=%r buffered", query)
    return {"message": "Searched", "query": query}


@app.get("/batch/stats")
def batch_stats():
    """Write-batching metrics for the performance report.

    `db_writes_without_batching` is the naive baseline (one write per search);
    `db_writes_with_batching` is what we actually issued. Their ratio is the
    write-reduction factor.
    """
    s = buffer.stats()
    received = s["searches_received"]
    s["db_writes_without_batching"] = received          # naive: 1 per search
    s["db_writes_with_batching"] = store.write_count    # actual DB transactions
    s["write_reduction_factor"] = (
        round(received / store.write_count, 1) if store.write_count else None
    )
    return s


@app.get("/trending")
def trending(n: int = Query(default=10, ge=1, le=50, description="how many to return")):
    """Currently-trending queries by recency score, independent of any prefix.

    Powers the UI's 'Trending searches' section. Scores decay over time, so a
    query that stops being searched gradually drops out of this list.
    """
    return {
        "trending": [
            {"query": q, "recency_score": round(score, 3), "count": store.get_count(q) or 0}
            for q, score in recency.top(n)
        ]
    }


@app.get("/cache/debug")
def cache_debug(prefix: str = Query(default="", description="prefix to inspect")):
    """Show which cache node owns the prefix and whether it's a hit or miss.

    Read-only: this does NOT change cache contents or hit/miss counters, so
    inspecting the cache never distorts the reported hit rate.
    """
    return cache.debug(prefix.strip().lower())


@app.get("/cache/stats")
def cache_stats():
    """Aggregate cache metrics: hits, misses, hit rate, and per-node breakdown.
    The per-node sizes also show the consistent-hash load distribution."""
    return cache.metrics()


@app.get("/health")
def health():
    return {"status": "ok", "queries_indexed": store.total_queries()}
