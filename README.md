# Typeahead Search Engine

A low-latency search **typeahead / autocomplete** system. As you type a prefix it returns
the most popular matching queries; when you submit a search it records the query so
popularity updates over time. Suggestions are served from an **in-memory distributed
cache** whose nodes are addressed by a **consistent-hash ring**, falling back to a trie
index over a durable SQLite store.

This is a backend-focused systems project: the interesting parts are how query-count data
is stored, how suggestions are served fast, and how the cache is distributed.

## How it works

The read and write paths are deliberately separated because they have opposite pressures —
reads happen on every keystroke and must be instant; writes happen on every search and
should be cheap.

```
                 Browser (Next.js, debounced)
                            │  /api/*  (Next rewrites → FastAPI)
                            ▼
                     FastAPI backend
        ┌───────────────────┴─────────────────────┐
   READ │ GET /suggest                             │ WRITE  POST /search
        ▼                                          ▼
  Distributed cache  ──miss──▶  Trie index   increment count → SQLite
  (N nodes, consistent              (top-K per      + invalidate the
   hashing, TTL+LRU)                 prefix node)     affected prefixes
```

Three layers, fast → durable:

1. **Distributed cache** (`app/cache.py`, `app/ring.py`) — N logical nodes, each a bounded
   LRU store with per-entry TTL. A **consistent-hash ring** (SHA-1 → 32-bit, virtual nodes,
   binary-search lookup) decides which node owns each prefix, so adding/removing a node
   moves only ~1/N of keys instead of nearly all of them.
2. **Trie index** (`app/trie.py`) — a prefix tree with the top-10 completions precomputed
   at each node, so a lookup is `O(len(prefix))`, independent of dataset size. This is the
   cache's fallback (source of truth for the read path).
3. **SQLite store** (`app/store.py`) — durable `query → count`. The trie is rebuilt from it
   at startup.

## Tech stack

- **Backend:** Python 3.12 + FastAPI, SQLite (standard library only for the cache/ring)
- **Frontend:** Next.js 16 (React 19, TypeScript, Tailwind)
- **Dataset:** [Peter Norvig's `count_1w.txt`](https://norvig.com/ngrams/) — the 1/3 million
  most frequent English words with frequencies (top 100,000 loaded by default)

## Project structure

```
backend/
  app/
    store.py    SQLite source of truth (query → count)
    trie.py     in-memory prefix index, top-K per node
    ring.py     consistent-hash ring (virtual nodes)
    cache.py    distributed cache: N LRU+TTL nodes on the ring
    main.py     FastAPI app + endpoints
  scripts/
    load_data.py   load the dataset into SQLite
    ch_demo.py     demonstrate consistent-hashing distribution & ~1/N key movement
frontend/
  src/app/        Next.js pages
  src/components/  SearchBox (debounced typeahead, keyboard nav)
  src/lib/api.ts   backend client
```

## Getting started

### 1. Backend

```bash
cd backend
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt    # Windows
# macOS/Linux: source venv/bin/activate && pip install -r requirements.txt

# Download the dataset (one time):
mkdir -p data
curl -sSL -o data/count_1w.txt https://norvig.com/ngrams/count_1w.txt

# Load it into SQLite (top 100k by default; use --limit 0 for all 333,333):
./venv/Scripts/python.exe scripts/load_data.py

# Run the API:
./venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --log-level info
```

Interactive API docs: http://127.0.0.1:8000/docs

### 2. Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

The frontend proxies `/api/*` to the backend (see `frontend/next.config.ts`), so no CORS
setup is needed. Start the backend first.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/suggest?q=<prefix>` | Up to 10 prefix matches, sorted by count. Served cache-aside. |
| `POST` | `/search` | Record a submitted search; returns `{"message": "Searched", ...}`. Increments the count and invalidates affected cache entries. |
| `GET`  | `/cache/debug?prefix=<prefix>` | Which cache node owns the prefix, whether it is a hit or miss, TTL remaining, and the ring placement. Read-only. |
| `GET`  | `/cache/stats` | Aggregate hit/miss counts, hit rate, and per-node sizes. |
| `GET`  | `/health` | Service status and number of indexed queries. |

Example:

```bash
curl "http://127.0.0.1:8000/suggest?q=java"
curl -X POST "http://127.0.0.1:8000/search" -H "Content-Type: application/json" -d '{"q":"java"}'
curl "http://127.0.0.1:8000/cache/debug?prefix=java"
```

## Consistent-hashing demo

```bash
cd backend
./venv/Scripts/python.exe scripts/ch_demo.py
```

Sample output (100,000 keys, 3 nodes, 150 virtual nodes each):

```
1) Distribution: cache-0 34.9% | cache-1 31.5% | cache-2 33.6%   (≈ even)
2) Add a 4th node (3 → 4):  ~22% of keys move    (ideal ~25%)
4) Naive hash % N (3 → 4):  ~75% of keys move    (cache-miss storm)
```

This shows the two properties that make consistent hashing worthwhile: roughly even load
across nodes, and minimal key movement when the node set changes.
