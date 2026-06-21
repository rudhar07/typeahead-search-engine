// page.tsx — the home page. This is a Server Component (no "use client"); it
// just lays out the page and renders the interactive <SearchBox/> island.

import SearchBox from "@/components/SearchBox";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center bg-slate-50 px-4 pt-24 pb-16">
      <div className="mb-10 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900">
          Search Typeahead
        </h1>
        <p className="mt-2 text-slate-500">
          Type to see popular suggestions, ranked by search count.
        </p>
      </div>

      <SearchBox />

      <p className="mt-12 text-xs text-slate-400">
        ↑ / ↓ to navigate · Enter to search · Esc to close
      </p>
    </main>
  );
}
