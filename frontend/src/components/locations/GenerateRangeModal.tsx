import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { locationsApi, type LocationRow } from "../../api/locations";
import { last, pathLabel, pathNames } from "../../lib/locations";
import { HelpLink } from "../help/HelpLink";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LocationPicker } from "./LocationPicker";
import { useInvalidateLocations } from "./useLocations";

/** Clickable examples. The hint is what each one makes. */
const EXAMPLES: { pattern: string; hint: string }[] = [
  { pattern: "Box{01-05}", hint: "Box01 … Box05" },
  { pattern: "Bin-{A-F}", hint: "Bin-A … Bin-F" },
  { pattern: "Shelf {1-3}", hint: "Shelf 1 … Shelf 3" },
  { pattern: "{A-D}{1-6}", hint: "A1 … D6, 24 in a grid" },
  { pattern: "Col{AA-AD}", hint: "Col AA … Col AD" },
];

/** First six and last six with a gap between, so a long range reads at a glance. */
function previewChips<T>(names: T[]): (T | "gap")[] {
  if (names.length <= 14) return names;
  return [...names.slice(0, 6), "gap" as const, ...names.slice(-6)];
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function GenerateRangeModal({
  rows,
  initialParentId,
  onClose,
  onOpenLocation,
  onPrint,
}: {
  rows: LocationRow[];
  initialParentId: number | null;
  onClose: () => void;
  /** After creating: jump to the new locations' parent. */
  onOpenLocation: (parentId: number | null) => void;
  /** After creating: print labels for the new locations. */
  onPrint: (ids: number[]) => void;
}) {
  const [parentId, setParentId] = useState<number | null>(initialParentId);
  const [pattern, setPattern] = useState("");
  const debounced = useDebounced(pattern.trim(), 250);
  const invalidate = useInvalidateLocations();

  const preview = useQuery({
    queryKey: ["range-preview", parentId, debounced],
    queryFn: () => locationsApi.previewRange(parentId, debounced),
    enabled: debounced !== "",
    placeholderData: (previous) => previous,
  });
  const create = useMutation({
    mutationFn: () => locationsApi.generateRange(parentId, pattern.trim()),
    onSuccess: invalidate,
  });
  const undo = useMutation({
    mutationFn: async () => {
      // Safe because nothing has been placed in them yet — archiving needs a location empty.
      await Promise.all((create.data ?? []).map((l) => locationsApi.archive(l.id)));
    },
    onSuccess: () => {
      invalidate();
      create.reset();
    },
  });

  const data = pattern.trim() === "" ? undefined : preview.data;
  const stale = pattern.trim() !== debounced;
  const error = data?.error ?? null;
  const names = data?.names ?? [];
  const skipped = names.filter((n) => n.exists).length;
  const newCount = data?.new_count ?? 0;
  const parentLabel = parentId != null ? pathLabel(pathNames(rows, parentId)) : "the top level";

  if (create.isSuccess && create.data) {
    const made = create.data.length;
    return (
      <Modal title="Generate a range of locations" onClose={onClose} footer={<DoneButton onClose={onClose} />}>
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <div
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-green-100 text-lg text-green-800"
          >
            ✓
          </div>
          <h3 className="text-base font-semibold">
            Created {made} location{made === 1 ? "" : "s"} in {parentId != null ? last(pathNames(rows, parentId)) : "the top level"}
          </h3>
          <p className="max-w-sm text-sm text-slate-600">
            Each has its own scan code, which stays the same if you rename or move it.
          </p>
          <button
            onClick={() => onPrint((create.data ?? []).map((l) => l.id))}
            className="mt-2 rounded-md bg-slate-900 px-5 py-2 text-sm text-white"
          >
            Print labels for these
          </button>
          <div className="mt-1 flex gap-4 text-sm">
            <button onClick={() => onOpenLocation(parentId)} className="text-blue-700 hover:underline">
              Open in Locations
            </button>
            <button onClick={() => undo.mutate()} disabled={undo.isPending} className="text-slate-600 hover:underline">
              Undo (archive these)
            </button>
          </div>
          <ErrorBanner error={undo.error} />
        </div>
      </Modal>
    );
  }

  const chips = previewChips(names);
  return (
    <Modal
      title="Generate a range of locations"
      maxWidth="max-w-xl"
      onClose={onClose}
      footer={
        <>
          <span className="mr-auto self-center text-xs">
            <HelpLink doc="locations" anchor="generate-a-range">
              How patterns work
            </HelpLink>
          </span>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={!!error || newCount === 0 || stale || create.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {newCount > 0 && !error ? `Create ${newCount} location${newCount === 1 ? "" : "s"}` : "Create locations"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          Create inside
          <LocationPicker rows={rows} value={parentId} onChange={setParentId} allowTopLevel label="Create inside" />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="range-pattern" className="text-xs font-medium text-slate-700">
            Pattern
          </label>
          <input
            id="range-pattern"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            spellCheck={false}
            placeholder="Box{01-05}"
            aria-invalid={!!error}
            className={`rounded border px-2 py-1.5 font-mono text-sm ${error ? "border-red-600" : "border-slate-300"}`}
          />
          <p className={`text-xs ${error ? "text-red-700" : "text-slate-500"}`} role={error ? "alert" : undefined}>
            {error ?? "Padding follows the start: {01-05} gives 01, 02 … 05."}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-slate-500">Try</span>
            {EXAMPLES.map((e) => (
              <button
                key={e.pattern}
                type="button"
                title={e.hint}
                onClick={() => setPattern(e.pattern)}
                className={`rounded-full border px-2.5 py-0.5 font-mono text-xs hover:bg-slate-100 ${
                  pattern === e.pattern ? "border-slate-900 bg-slate-100" : "border-slate-300"
                }`}
              >
                {e.pattern}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50">
          <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2">
            <b className="text-[12.5px] font-semibold">Preview</b>
            <span
              className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                error || names.length === 0 ? "bg-slate-100 text-slate-600" : "bg-green-100 text-green-800"
              }`}
            >
              {error || names.length === 0 ? "No preview" : `${names.length} location${names.length === 1 ? "" : "s"}`}
            </span>
            {skipped > 0 && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">
                {skipped} skipped
              </span>
            )}
          </div>
          <div className="flex min-h-[60px] flex-wrap gap-1.5 p-3" aria-live="polite">
            {error || names.length === 0 ? (
              <span className="text-xs text-slate-500">
                {error ? "Fix the pattern to see what it would make." : `Names will appear here, inside ${parentLabel}.`}
              </span>
            ) : (
              chips.map((n, i) =>
                n === "gap" ? (
                  <span key={`gap-${i}`} className="px-2 py-0.5 text-xs italic text-slate-500">
                    … {names.length - 12} more …
                  </span>
                ) : (
                  <span
                    key={n.name}
                    className={`rounded border px-2 py-0.5 text-xs ${
                      n.exists
                        ? "border-dashed border-slate-300 bg-slate-50 text-slate-500 line-through"
                        : "border-slate-200 bg-white"
                    }`}
                  >
                    {n.name}
                    {n.exists && <span className="ml-1.5 text-[10.5px] no-underline">already exists</span>}
                  </span>
                ),
              )
            )}
          </div>
        </div>
        <p className="text-xs text-slate-500">
          Nothing is saved until you press Create. Locations that already exist here are skipped, never overwritten.
        </p>
        <ErrorBanner error={create.error} />
      </div>
    </Modal>
  );
}

function DoneButton({ onClose }: { onClose: () => void }) {
  return (
    <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
      Done
    </button>
  );
}
