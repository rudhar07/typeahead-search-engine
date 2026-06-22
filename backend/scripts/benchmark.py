"""Measure the metrics required by the performance report:

  1. /suggest latency (p50 / p95 / p99) — cache miss vs cache hit
  2. cache hit rate over a realistic repeated-prefix workload
  3. database-write reduction from batching

The backend must be running on http://127.0.0.1:8000 (ideally freshly started so
the cache begins empty). Run from the backend/ directory:

    ./venv/Scripts/python.exe scripts/benchmark.py
"""
from __future__ import annotations

import http.client
import json
import time

HOST, PORT = "127.0.0.1", 8000
# ~115 two-letter prefixes — a realistic typeahead working set.
PREFIXES = [a + b for a in "abcdefghijklmnopqrstuvw" for b in "aeiou"]
WARM_ROUNDS = 20  # repeats of the working set for the hit-path measurement

# One reused keep-alive connection, so we measure SERVER latency, not the cost
# of opening a fresh TCP connection on every request.
_conn = http.client.HTTPConnection(HOST, PORT, timeout=15)


def _request(method: str, path: str, body: str | None = None, headers: dict | None = None):
    _conn.request(method, path, body=body, headers=headers or {})
    resp = _conn.getresponse()
    return resp.status, resp.read()


def _get(path: str) -> bytes:
    return _request("GET", path)[1]


def _get_json(path: str) -> dict:
    return json.loads(_get(path))


def _post_search(q: str) -> None:
    _request("POST", "/search", json.dumps({"q": q}), {"Content-Type": "application/json"})


def timed(path: str) -> float:
    t = time.perf_counter()
    _get(path)
    return (time.perf_counter() - t) * 1000.0  # milliseconds


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    k = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return s[k]


def line(name: str, xs: list[float]) -> str:
    return (
        f"  {name:16} n={len(xs):5d}   "
        f"p50={pct(xs,50):6.2f}ms   p95={pct(xs,95):6.2f}ms   "
        f"p99={pct(xs,99):6.2f}ms   max={max(xs):6.2f}ms"
    )


def main() -> None:
    _get("/health")  # warm up the connection
    before = _get_json("/cache/stats")

    # 1) cold path: first request per prefix => cache MISS (compute from trie)
    cold = [timed(f"/suggest?q={p}") for p in PREFIXES]

    # 2) warm path: repeat the working set => cache HIT
    warm: list[float] = []
    for _ in range(WARM_ROUNDS):
        for p in PREFIXES:
            warm.append(timed(f"/suggest?q={p}"))

    # 3) trending path (recency-aware), warm
    trend = [timed(f"/suggest?q={p}&mode=trending") for p in PREFIXES for _ in range(3)]

    after = _get_json("/cache/stats")
    hits = after["hits"] - before["hits"]
    misses = after["misses"] - before["misses"]
    hit_rate = hits / (hits + misses) if (hits + misses) else 0.0

    print("=" * 72)
    print("SUGGEST LATENCY")
    print(line("cold (miss)", cold))
    print(line("warm (hit)", warm))
    print(line("trending", trend))
    print()
    print("CACHE HIT RATE (this benchmark's requests)")
    print(f"  hits={hits}  misses={misses}  hit_rate={hit_rate*100:.1f}%")

    # 4) write reduction: submit many searches, wait for a flush, read the delta
    b0 = _get_json("/batch/stats")
    queries = ["java", "python", "search", "news", "weather", "maps", "ipad", "game"]
    N = 500
    for i in range(N):
        _post_search(queries[i % len(queries)])
    time.sleep(2.5)  # allow the periodic flush to run
    b1 = _get_json("/batch/stats")
    d_searches = b1["searches_received"] - b0["searches_received"]
    d_writes = b1["db_writes_with_batching"] - b0["db_writes_with_batching"]
    print()
    print("WRITE REDUCTION (batching)")
    print(f"  searches submitted:        {d_searches}")
    print(f"  DB writes WITHOUT batching: {d_searches}  (1 per search)")
    print(f"  DB writes WITH batching:    {d_writes}")
    print(f"  reduction factor:          {d_searches / d_writes:.0f}x" if d_writes else "  (no writes)")
    print("=" * 72)


if __name__ == "__main__":
    main()
