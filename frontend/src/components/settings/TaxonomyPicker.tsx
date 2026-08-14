import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { listingProfilesApi } from "../../api/listingProfiles";

/**
 * Picks an Etsy category by name.
 *
 * Search rather than a dropdown because Etsy's taxonomy runs to thousands of nodes, and
 * search on the *full path* rather than the leaf name because leaf names repeat — several
 * nodes are called "Stands", and only "Electronics > Electronics Stands" tells you which.
 *
 * Once chosen it shows the path, never the id. The id is what gets stored and what Etsy
 * needs, but it is meaningless to a person and appears nowhere in Etsy's own seller UI —
 * which is exactly why typing one by hand was never a reasonable thing to ask.
 */
export function TaxonomyPicker({
  value,
  valueLabel,
  onChange,
}: {
  value: number | null;
  /** Path of the current selection, when we already know it. */
  valueLabel?: string | null;
  onChange: (id: number | null, path: string | null) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [debounced, setDebounced] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  // Typing shouldn't fire a request per keystroke; the tree is cached server-side but the
  // round trip is not free.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const onClickAway = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, []);

  const { data: results, isFetching } = useQuery({
    queryKey: ["platforms", "etsy", "taxonomy", debounced],
    queryFn: () => listingProfilesApi.searchEtsyTaxonomy(debounced),
    enabled: open && debounced.trim().length >= 2,
  });

  if (value !== null && !open) {
    return (
      <div className="flex items-center gap-2">
        <span className="flex-1 truncate rounded border border-slate-300 bg-white px-2 py-1">
          {valueLabel || `Category ${value}`}
        </span>
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            setSearch("");
          }}
          className="shrink-0 text-xs text-slate-600 underline"
        >
          Change
        </button>
      </div>
    );
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        className="w-full rounded border border-slate-300 px-2 py-1"
        value={search}
        autoFocus={open}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search Etsy categories, e.g. desk storage"
      />
      {open && (
        <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded border border-slate-300 bg-white shadow">
          {debounced.trim().length < 2 && (
            <p className="px-2 py-2 text-xs text-slate-500">Type at least two characters.</p>
          )}
          {debounced.trim().length >= 2 && isFetching && (
            <p className="px-2 py-2 text-xs text-slate-500">Searching…</p>
          )}
          {results?.length === 0 && !isFetching && (
            <p className="px-2 py-2 text-xs text-slate-500">No categories match "{debounced}".</p>
          )}
          {results?.map((node) => (
            <button
              key={node.id}
              type="button"
              onClick={() => {
                onChange(node.id, node.path);
                setOpen(false);
                setSearch("");
              }}
              className="block w-full px-2 py-1.5 text-left text-xs hover:bg-slate-100"
            >
              <span className="font-medium">{node.name}</span>
              <span className="block text-slate-500">{node.path}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
