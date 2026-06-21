"use client";

// TrendingSection — shows the currently-trending queries (by recency score).
// It polls /trending periodically so the list updates live as searches come in.

import { useEffect, useState } from "react";
import { fetchTrending, type TrendingItem } from "@/lib/api";

const REFRESH_MS = 4000;

export default function TrendingSection() {
  const [items, setItems] = useState<TrendingItem[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const data = await fetchTrending(8);
        if (active) {
          setItems(data);
          setError(false);
        }
      } catch {
        if (active) setError(true);
      }
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  return (
    <section className="mt-14 w-full max-w-xl">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Trending searches
      </h2>

      {error && <p className="text-sm text-slate-400">Could not load trending.</p>}

      {!error && items.length === 0 && (
        <p className="text-sm text-slate-400">
          No recent activity yet — submit a few searches to see trends appear.
        </p>
      )}

      {items.length > 0 && (
        <ol className="space-y-1">
          {items.map((t, i) => (
            <li
              key={t.query}
              className="flex items-center justify-between rounded-lg bg-white px-3 py-2 text-sm shadow-sm"
            >
              <span className="flex items-center gap-2">
                <span className="w-5 text-right text-slate-400">{i + 1}.</span>
                <span className="text-slate-800">{t.query}</span>
              </span>
              <span className="text-xs tabular-nums text-slate-400">
                score {t.recency_score.toFixed(1)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
