"use client";

// SearchBox.tsx — the typeahead UI.
//
// This is a Client Component ("use client") because it has interactive state
// (input value, suggestion list, keyboard highlight) and uses browser-only
// hooks (useState/useEffect/useRef). Server Components can't hold this state.
//
// Key behaviours required by the assignment:
//   - debounced suggestion fetching (don't call the API on every keystroke)
//   - dropdown that updates as you type
//   - keyboard nav (↑/↓/Enter/Esc)
//   - loading & error states
//   - submit on Enter or button click -> POST /search, show dummy response

import { useEffect, useRef, useState } from "react";
import {
  fetchSuggestions,
  postSearch,
  type SearchResponse,
  type Suggestion,
} from "@/lib/api";

const DEBOUNCE_MS = 200; // wait for a typing pause before calling the API

export default function SearchBox() {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1); // -1 = nothing highlighted
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSearch, setLastSearch] = useState<SearchResponse | null>(null);
  const [trending, setTrending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // ---- Debounced suggestion fetching --------------------------------------
  // This effect re-runs every time `query` changes. We wait DEBOUNCE_MS before
  // actually fetching. If the user types again within that window, the cleanup
  // function cancels the pending timer AND aborts any in-flight request — so a
  // fast burst of keystrokes collapses into a single backend call.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setSuggestions([]);
      setOpen(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        setLoading(true);
        setError(null);
        const results = await fetchSuggestions(
          q,
          trending ? "trending" : "basic",
          controller.signal,
        );
        setSuggestions(results);
        setOpen(true);
        setHighlight(-1);
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError("Could not load suggestions");
          setSuggestions([]);
        }
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer); // cancel the not-yet-fired request (the debounce)
      controller.abort(); // cancel an already-in-flight request
    };
  }, [query, trending]); // re-fetch when the ranking mode is toggled

  // ---- Submit a search ----------------------------------------------------
  async function submitSearch(term: string) {
    const q = term.trim();
    if (!q) return;
    setOpen(false);
    setHighlight(-1);
    try {
      const res = await postSearch(q);
      setLastSearch(res);
    } catch {
      setError("Search submission failed");
    }
  }

  // ---- Keyboard navigation ------------------------------------------------
  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // If a suggestion is highlighted, submit that; otherwise submit what's typed.
      if (highlight >= 0 && highlight < suggestions.length) {
        const chosen = suggestions[highlight].query;
        setQuery(chosen);
        submitSearch(chosen);
      } else {
        submitSearch(query);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setHighlight(-1);
    }
  }

  function chooseSuggestion(s: Suggestion) {
    setQuery(s.query);
    submitSearch(s.query);
    inputRef.current?.focus();
  }

  const showDropdown = open && (loading || error || suggestions.length > 0);

  return (
    <div className="w-full max-w-xl">
      <div className="mb-2 flex items-center justify-end text-sm text-slate-600">
        <label className="flex cursor-pointer select-none items-center gap-2">
          <input
            type="checkbox"
            checked={trending}
            onChange={(e) => setTrending(e.target.checked)}
            className="h-4 w-4 accent-indigo-600"
          />
          Trending (recency-aware) ranking
        </label>
      </div>
      <div className="relative">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              onFocus={() => suggestions.length > 0 && setOpen(true)}
              placeholder="Search…  (try 'java', 'sea', 'pyth')"
              autoComplete="off"
              role="combobox"
              aria-expanded={showDropdown}
              aria-controls="suggestion-list"
              className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200"
            />
            {loading && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
                …
              </span>
            )}
          </div>
          <button
            onClick={() => submitSearch(query)}
            className="rounded-xl bg-indigo-600 px-5 py-3 font-medium text-white shadow-sm transition hover:bg-indigo-700 active:bg-indigo-800"
          >
            Search
          </button>
        </div>

        {/* Suggestion dropdown */}
        {showDropdown && (
          <ul
            id="suggestion-list"
            role="listbox"
            className="absolute z-10 mt-2 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg"
          >
            {error && (
              <li className="px-4 py-3 text-sm text-red-600">{error}</li>
            )}
            {!error && suggestions.length === 0 && !loading && (
              <li className="px-4 py-3 text-sm text-slate-400">No matches</li>
            )}
            {suggestions.map((s, i) => (
              <li
                key={s.query}
                role="option"
                aria-selected={i === highlight}
                onMouseDown={(e) => {
                  // onMouseDown (not onClick) so it fires before the input blurs.
                  e.preventDefault();
                  chooseSuggestion(s);
                }}
                onMouseEnter={() => setHighlight(i)}
                className={`flex cursor-pointer items-center justify-between px-4 py-2.5 text-sm ${
                  i === highlight ? "bg-indigo-50" : "hover:bg-slate-50"
                }`}
              >
                <span className="text-slate-800">{s.query}</span>
                <span className="text-xs tabular-nums text-slate-400">
                  {s.count.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Dummy search response (assignment requires displaying it) */}
      {lastSearch && (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <span className="font-semibold">{lastSearch.message}</span>
          {lastSearch.query && (
            <>
              {" — "}
              <span className="font-mono">{lastSearch.query}</span> is now at{" "}
              <span className="font-semibold tabular-nums">
                {lastSearch.count.toLocaleString()}
              </span>{" "}
              searches
            </>
          )}
        </div>
      )}
    </div>
  );
}
