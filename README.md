# Search Typeahead System

A search typeahead (autocomplete) system: suggests popular queries as you type, records
searches, serves suggestions from a distributed cache with **consistent hashing**, ranks
by popularity **and recency**, and reduces database load with **batched writes**.

> Backend focus: how query-count data is stored, how suggestions are served with low
> latency, how the cache is distributed, and how write pressure is reduced.

## Tech stack
- **Backend:** Python 3.12 + FastAPI (SQLite as the durable store)
- **Frontend:** Next.js (React)
- **Index:** in-memory trie with precomputed top-K suggestions per node
- **Cache:** in-process cache nodes addressed by a consistent-hash ring
- **Dataset:** [Peter Norvig's `count_1w.txt`](https://norvig.com/ngrams/) — the 1/3
  million most frequent English words with frequencies (top 100k loaded by default)

## Architecture (high level)
```
Browser (Next.js)  ──/api──▶  FastAPI
   debounced /suggest                 ├─ READ:  cache (consistent hashing) ─miss▶ trie
   /search on submit                  └─ WRITE: buffer ─(aggregate, flush)▶ SQLite
```

## Getting started

### 1. Backend
```bash
cd backend
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# (macOS/Linux: source venv/bin/activate && pip install -r requirements.txt)

# Download the dataset (one time):
mkdir -p data
curl -sSL -o data/count_1w.txt https://norvig.com/ngrams/count_1w.txt

# Load it into SQLite (top 100k; use --limit 0 for all 333,333):
./venv/Scripts/python.exe scripts/load_data.py

# Run the API:
./venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```
API docs: http://127.0.0.1:8000/docs

### 2. Frontend
```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

## API
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/suggest?q=<prefix>` | Up to 10 prefix matches, sorted by score |
| POST | `/search` | Record a submitted search (returns `{"message":"Searched"}`) |
| GET | `/cache/debug?prefix=<prefix>` | Which cache node owns the prefix + hit/miss |
| GET | `/health` | Service status + indexed query count |

_(Endpoints are added milestone by milestone; see commit history.)_

## Status
- [x] Dataset ingestion + `GET /suggest` (trie)
- [ ] Frontend search UI
- [ ] `POST /search` + count updates
- [ ] Distributed cache + consistent hashing + `/cache/debug`
- [ ] Trending (recency-aware ranking)
- [ ] Batch writes
- [ ] Performance report
