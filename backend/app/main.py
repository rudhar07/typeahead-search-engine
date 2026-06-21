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

from app.cache import DistributedCache
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

# Module-level singletons: one store, one trie, one distributed cache — shared by
# all requests. The cache has 3 logical nodes addressed by a consistent-hash ring.
store = Store()
trie = Trie(k=10)
cache = DistributedCache([f"cache-{i}" for i in range(3)])


def _fmt(pairs):
    """Turn [(query, count), ...] into the API's [{"query":..,"count":..}, ...]."""
    return [{"query": q, "count": c} for q, c in pairs]


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
    yield
    # ---- shutdown: nothing to clean up yet ----------------------------------


app = FastAPI(title="Search Typeahead", lifespan=lifespan)


@app.get("/suggest")
def suggest(q: str = Query(default="", description="prefix the user has typed")):
    """Typeahead suggestions for a prefix, served via CACHE-ASIDE.

    Flow:
      1. normalize prefix (lowercase) — must match the key form used everywhere
      2. empty prefix -> [] without touching the cache (don't pollute it)
      3. ask the cache (ring routes the prefix to its owning node)
      4. HIT  -> return the cached top-10 immediately
      5. MISS -> compute from the trie, store it in the owning node, return it

    Functional requirements handled here:
      - at most 10 / start with prefix / sorted by count  -> trie + top-k
      - empty / missing / mixed-case / no-match            -> handled gracefully
    """
    prefix = q.strip().lower()
    if not prefix:
        return {"prefix": prefix, "suggestions": []}

    owner, cached = cache.get_routed(prefix)
    if cached is not None:  # [] is a valid (negative-cache) hit, hence "is not None"
        logger.info("suggest prefix=%r node=%s HIT", prefix, owner)
        return {"prefix": prefix, "suggestions": _fmt(cached)}

    results = trie.suggest(prefix, limit=10)  # cache miss -> fall back to the trie
    cache.set(prefix, results)                # populate the owning node (with TTL)
    logger.info("suggest prefix=%r node=%s MISS", prefix, owner)
    return {"prefix": prefix, "suggestions": _fmt(results)}


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
        return {"message": "Searched", "query": "", "count": 0}
    new_count = store.increment(query, 1)
    trie.insert(query, new_count)  # keep the in-memory index in sync
    # Invalidate cached suggestions that could now be stale: exactly the prefixes
    # of this query (each routed to its own owning node). TTL is the safety net
    # for anything not covered here.
    invalidated = cache.invalidate_prefixes(query)
    logger.info("search q=%r count=%d invalidated=%d", query, new_count, invalidated)
    return {"message": "Searched", "query": query, "count": new_count}


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
