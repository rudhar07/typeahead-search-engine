"""
main.py — FastAPI application.

MILESTONE 1 scope: expose GET /suggest, backed by the in-memory Trie which is
built at startup from the SQLite store (the durable source of truth).

Later milestones add: POST /search, the distributed cache + /cache/debug,
trending, and batch writes. We grow this file one milestone at a time.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.store import Store
from app.trie import Trie

# These are module-level singletons: one store, one trie, shared by all requests.
store = Store()
trie = Trie(k=10)


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
    """Typeahead suggestions for a prefix.

    Functional requirements handled here:
      - at most 10 results                  -> limit=10
      - results start with the prefix       -> trie guarantees this
      - sorted by count descending          -> trie stores top-k sorted
      - empty / missing input -> []         -> handled below
      - mixed-case input                    -> we lowercase the prefix
      - prefix with no matches -> []        -> trie.suggest returns []
    """
    prefix = q.strip().lower()
    if not prefix:
        return {"prefix": prefix, "suggestions": []}
    results = trie.suggest(prefix, limit=10)
    return {
        "prefix": prefix,
        "suggestions": [{"query": query, "count": count} for query, count in results],
    }


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
    return {"message": "Searched", "query": query, "count": new_count}


@app.get("/health")
def health():
    return {"status": "ok", "queries_indexed": store.total_queries()}
