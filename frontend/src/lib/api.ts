// api.ts — typed helpers for talking to the backend.
//
// Every call hits "/api/..." which Next.js rewrites (see next.config.ts) to the
// FastAPI backend at http://127.0.0.1:8000. Because it's same-origin from the
// browser's view, there's no CORS to configure.

export type Suggestion = { query: string; count: number };
export type SuggestResponse = { prefix: string; mode: string; suggestions: Suggestion[] };
export type SearchResponse = { message: string; query: string; count: number };
export type TrendingItem = { query: string; recency_score: number; count: number };

export type RankingMode = "basic" | "trending";

const API = "/api";

/**
 * Fetch typeahead suggestions for a prefix.
 * `mode` selects basic (all-time count) or trending (recency-aware) ranking.
 * Accepts an AbortSignal so an in-flight request can be cancelled when the user
 * keeps typing (part of how debouncing avoids wasted backend work).
 */
export async function fetchSuggestions(
  prefix: string,
  mode: RankingMode = "basic",
  signal?: AbortSignal,
): Promise<Suggestion[]> {
  const res = await fetch(
    `${API}/suggest?q=${encodeURIComponent(prefix)}&mode=${mode}`,
    { signal },
  );
  if (!res.ok) throw new Error(`suggest failed: ${res.status}`);
  const data: SuggestResponse = await res.json();
  return data.suggestions ?? [];
}

/** Fetch the currently-trending queries (by recency), for the trending section. */
export async function fetchTrending(limit = 8): Promise<TrendingItem[]> {
  const res = await fetch(`${API}/trending?n=${limit}`);
  if (!res.ok) throw new Error(`trending failed: ${res.status}`);
  const data: { trending: TrendingItem[] } = await res.json();
  return data.trending ?? [];
}

/** Submit a search. The backend records it and returns the dummy "Searched". */
export async function postSearch(query: string): Promise<SearchResponse> {
  const res = await fetch(`${API}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ q: query }),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}
