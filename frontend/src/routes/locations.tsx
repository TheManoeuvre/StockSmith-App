import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  locationsApi,
  type LocationContent,
  type LocationHistoryEntry,
  type LocationRow,
} from "../api/locations";
import { stockTakesApi } from "../api/stockTakes";
import { AddLocationModal } from "../components/locations/AddLocationModal";
import { GenerateRangeModal } from "../components/locations/GenerateRangeModal";
import { LocationTree } from "../components/locations/LocationTree";
import { EmptyIntoModal, MoveLocationModal, MoveLocationsModal, PlaceItemModal } from "../components/locations/MoveModals";
import { PlaceHereModal } from "../components/locations/PlaceHereModal";
import { useInvalidateLocations, useLocationTree } from "../components/locations/useLocations";
import { Badge } from "../components/common/Badge";
import { ConfirmDialog } from "../components/common/ConfirmDialog";
import { CsvImportExport } from "../components/common/CsvImportExport";
import { CopyButton } from "../components/common/CopyButton";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { FilterTabs } from "../components/common/FilterTabs";
import { Th } from "../components/common/ListTable";
import { HelpLink } from "../components/help/HelpLink";
import { displayQty, qtyWithUnit } from "../lib/format";
import { PAGE_HELP } from "../lib/help";
import { compareNames, type TreeFilter } from "../lib/locations";

/**
 * Locations (docs/plan-locations.md §8): the tree on the left, the selected location on the
 * right. The selection lives in the URL (`?loc=`) so other pages can link straight to a place and
 * the back button works.
 */
export const Route = createFileRoute("/locations")({
  validateSearch: (search: Record<string, unknown>): { loc?: number } => {
    const n = Number(search.loc);
    return Number.isInteger(n) && n > 0 ? { loc: n } : {};
  },
  component: LocationsPage,
});

const INTRO_KEY = "stocksmith-locations-intro-dismissed";

function readIntroDismissed(): boolean {
  try {
    return window.localStorage.getItem(INTRO_KEY) === "1";
  } catch {
    return false;
  }
}

type Dialog =
  | { kind: "add"; parentId: number | null }
  | { kind: "range"; parentId: number | null }
  | { kind: "move" }
  | { kind: "moveMany" }
  | { kind: "empty" }
  | { kind: "placeHere" }
  | { kind: "moveItem"; item: LocationContent }
  | { kind: "archive" }
  | { kind: "delete" };

function LocationsPage() {
  const { loc } = Route.useSearch();
  const navigate = useNavigate({ from: "/locations" });
  const { data: tree, isLoading, error } = useLocationTree();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<TreeFilter>("all");
  const [showArchived, setShowArchived] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [introDismissed, setIntroDismissed] = useState(readIntroDismissed);
  const [scan, setScan] = useState("");
  const [scanMiss, setScanMiss] = useState(false);

  const rows = useMemo(() => tree?.locations ?? [], [tree]);
  const live = rows.filter((r) => !r.archived);
  // Ctrl/Cmd-click ticks locations for bulk actions; a plain click goes back to a single selection.
  const [multi, setMulti] = useState<Set<number>>(new Set());
  const select = (id: number | null) => {
    setMulti(new Set());
    void navigate({ search: id == null ? {} : { loc: id }, replace: false });
  };

  // Land on something: the first top-level location, so the page is never an empty right pane.
  const firstId = useMemo(
    () => [...live].filter((r) => r.parent_id == null).sort((a, b) => compareNames(a.name, b.name))[0]?.id ?? null,
    [live],
  );
  const selectedId = loc != null && rows.some((r) => r.id === loc) ? loc : firstId;
  // Ctrl-clicking while nothing is ticked starts from the location already open.
  const toggleMulti = (id: number) => {
    if (rows.find((r) => r.id === id)?.archived) return;
    const next = new Set(multi);
    if (next.size === 0 && selectedId != null && selectedId !== id) next.add(selectedId);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setMulti(next.size < 2 ? new Set() : next);
  };
  const multiIds = [...multi].filter((id) => rows.some((r) => r.id === id));

  // Scan or type a LOC- code: a scanner types the code and presses Enter, which jumps to it.
  const goToCode = () => {
    const code = scan.trim().toLowerCase();
    if (!code) return;
    const hit = rows.find((r) => r.code.toLowerCase() === code);
    if (hit) {
      setScan("");
      setScanMiss(false);
      select(hit.id);
    } else {
      setScanMiss(true);
    }
  };

  const dismissIntro = () => {
    setIntroDismissed(true);
    try {
      window.localStorage.setItem(INTRO_KEY, "1");
    } catch {
      /* private mode: the intro simply comes back next time */
    }
  };

  if (isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (error) return <ErrorBanner error={error} />;

  const isFirstRun = live.length === 0 && rows.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex items-center gap-2.5">
        <h1 className="text-xl font-semibold">Locations</h1>
        <HelpLink doc={PAGE_HELP.locations.doc} anchor={PAGE_HELP.locations.anchor} />
        <span className="flex-1" />
        {!isFirstRun && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              goToCode();
            }}
            className="flex items-center gap-2"
          >
            {scanMiss && <span className="text-xs text-amber-700">No location with that code</span>}
            <input
              value={scan}
              onChange={(e) => {
                setScan(e.target.value);
                setScanMiss(false);
              }}
              placeholder="Scan or type a LOC- code"
              aria-label="Scan or type a location code"
              className="w-[260px] rounded-md border border-slate-300 px-2.5 py-1.5 text-[12.5px]"
            />
          </form>
        )}
        {!isFirstRun && introDismissed && (
          <button
            onClick={() => {
              setIntroDismissed(false);
              try {
                window.localStorage.removeItem(INTRO_KEY);
              } catch {
                /* ignore */
              }
            }}
            className="text-xs text-slate-500 hover:underline"
          >
            About locations
          </button>
        )}
      </header>

      {/* A layout can be imported from a spreadsheet at any time, including before the first
          location exists: one row per location, path as "Workshop > Oils rack > Bin 1". */}
      <div className="flex justify-end">
        <CsvImportExport
          onExport={locationsApi.exportCsv}
          onImport={locationsApi.importCsv}
          invalidateKey={["locations", "location", "location-summaries", "stored-in"]}
          className="flex flex-col items-end gap-2"
        />
      </div>

      {isFirstRun ? (
        <FirstRun
          onAdd={() => setDialog({ kind: "add", parentId: null })}
          onRange={() => setDialog({ kind: "range", parentId: null })}
        />
      ) : (
        <>
          {!introDismissed && (
            <div className="flex items-start gap-3 rounded-lg bg-slate-50 p-3 text-[13px] leading-relaxed text-slate-700 ring-1 ring-slate-200">
              <p className="flex-1">
                Locations are the places you keep stock, and they nest: a box on a shelf in a room. Say how much of
                each item is in each place, and builds, sales and deliveries keep single-place items right for you.{" "}
                <HelpLink doc="locations">Learn more</HelpLink>
              </p>
              <button onClick={dismissIntro} className="flex-none rounded-md border border-slate-300 px-2.5 py-1 text-xs">
                Got it
              </button>
            </div>
          )}
          <div className="flex min-h-0 flex-1 gap-4">
            <section className="flex w-[330px] flex-none flex-col rounded-lg bg-white shadow-sm">
              <div className="flex gap-2 p-2.5">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search locations"
                  aria-label="Search locations"
                  className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                />
                <button
                  onClick={() => setDialog({ kind: "add", parentId: selectedId })}
                  className="flex-none rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white"
                >
                  Add location
                </button>
              </div>
              <FilterTabs
                tabs={[
                  { id: "all", label: "All", count: tree?.total_locations ?? 0 },
                  { id: "attention", label: "Attention", count: tree?.attention_locations ?? 0 },
                  { id: "empty", label: "Empty", count: tree?.empty_locations ?? 0 },
                ]}
                active={filter}
                onChange={(id) => setFilter(id as TreeFilter)}
              />
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                <LocationTree
                  rows={rows}
                  selectedId={selectedId}
                  onSelect={select}
                  query={query}
                  filter={filter}
                  showArchived={showArchived}
                  multiSelected={multi}
                  onToggleMulti={toggleMulti}
                />
              </div>
              <div className="flex items-center justify-between gap-2 border-t border-slate-200 px-3 py-2 text-xs text-slate-500">
                <span>
                  {tree?.total_locations} location{tree?.total_locations === 1 ? "" : "s"}
                  {tree && tree.attention_locations > 0 && ` · ${tree.attention_locations} need attention`}
                </span>
                {rows.some((r) => r.archived) && (
                  <label className="flex items-center gap-1.5">
                    <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
                    Show archived ({rows.filter((r) => r.archived).length})
                  </label>
                )}
              </div>
            </section>

            <section className="min-w-0 flex-1 overflow-y-auto">
              {multiIds.length > 1 ? (
                <div className="flex flex-col gap-2.5 rounded-lg bg-white p-4 shadow-sm">
                  <h2 className="text-base font-semibold">{multiIds.length} locations selected</h2>
                  <ul className="text-[13px] text-slate-700">
                    {multiIds.map((id) => (
                      <li key={id}>{rows.find((r) => r.id === id)?.name}</li>
                    ))}
                  </ul>
                  <div className="flex gap-2">
                    <ActionButton onClick={() => setDialog({ kind: "moveMany" })}>Move selected…</ActionButton>
                    <ActionButton onClick={() => setMulti(new Set())}>Clear selection</ActionButton>
                  </div>
                  <p className="text-xs text-slate-500">Ctrl-click (Cmd-click on a Mac) to add or remove locations.</p>
                </div>
              ) : selectedId != null ? (
                <LocationPane
                  key={selectedId}
                  locationId={selectedId}
                  rows={rows}
                  unplacedItems={tree?.unplaced_items ?? 0}
                  placeableItems={tree?.placeable_items ?? 0}
                  onSelect={select}
                  dialog={dialog}
                  setDialog={setDialog}
                />
              ) : (
                <p className="p-4 text-sm text-slate-500">Choose a location to see what’s in it.</p>
              )}
            </section>
          </div>
        </>
      )}

      {dialog?.kind === "add" && (
        <AddLocationModal
          rows={rows}
          initialParentId={dialog.parentId}
          onClose={() => setDialog(null)}
          onCreated={(created) => {
            setDialog(null);
            select(created.id);
          }}
        />
      )}
      {dialog?.kind === "moveMany" && (
        <MoveLocationsModal
          rows={rows}
          ids={multiIds}
          onClose={() => setDialog(null)}
          onMoved={() => {
            setDialog(null);
            setMulti(new Set());
          }}
        />
      )}
      {dialog?.kind === "range" && (
        <GenerateRangeModal
          rows={rows}
          initialParentId={dialog.parentId}
          onClose={() => setDialog(null)}
          onOpenLocation={(parentId) => {
            setDialog(null);
            select(parentId);
          }}
          onPrint={(ids) => {
            setDialog(null);
            void navigate({ to: "/location-labels", search: { ids: ids.join(",") } });
          }}
        />
      )}
    </div>
  );
}

/** Shown once, until a first location exists: what this is, and the two ways to start. */
function FirstRun({ onAdd, onRange }: { onAdd: () => void; onRange: () => void }) {
  const example: { name: string; depth: number; bold?: boolean }[] = [
    { name: "Workshop", depth: 0, bold: true },
    { name: "Wax shelf", depth: 1 },
    { name: "Oils rack", depth: 1 },
    { name: "Bin 1", depth: 2 },
    { name: "Bin 2", depth: 2 },
    { name: "Loft", depth: 0, bold: true },
  ];
  return (
    <section className="flex flex-wrap items-center justify-center gap-14 rounded-lg bg-white p-10 shadow-sm">
      <div className="flex max-w-[440px] flex-col gap-3.5">
        <h2 className="text-[22px] font-semibold">Where do you keep things?</h2>
        <p className="text-sm leading-relaxed text-slate-700">
          Add the places you store stock — a shelf, a box, a room — and put locations inside each other as deeply as
          you like. Then say how much of each material or product is in each one. It’s optional, and you can do it any
          time.
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2.5">
          <button onClick={onAdd} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
            Add a location
          </button>
          <button onClick={onRange} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Generate a range <code className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">Box{"{01-05}"}</code>
          </button>
        </div>
        <p className="text-[12.5px] text-slate-500">
          A range makes several at once: <code className="font-mono">Box{"{01-05}"}</code> creates Box01 to Box05, and
          you see the list before anything is saved. <HelpLink doc="locations" anchor="generate-a-range">Learn more</HelpLink>
        </p>
      </div>
      <figure className="min-w-[270px] rounded-lg border border-slate-200 bg-slate-50 px-5 py-4">
        <figcaption className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">For example</figcaption>
        {example.map((e) => (
          <div key={e.name} className={`py-0.5 text-[13px] ${e.bold ? "font-semibold" : ""}`} style={{ paddingLeft: e.depth * 20 }}>
            <span aria-hidden="true" className="mr-2 inline-block h-2.5 w-2.5 rounded-sm border border-slate-400 align-middle" />
            {e.name}
          </div>
        ))}
        <p className="mt-2 border-t border-slate-200 pt-2 text-xs text-slate-500">
          One level is fine too: just <code className="font-mono">Box A1</code>.
        </p>
      </figure>
    </section>
  );
}

function LocationPane({
  locationId,
  rows,
  unplacedItems,
  placeableItems,
  onSelect,
  dialog,
  setDialog,
}: {
  locationId: number;
  rows: LocationRow[];
  unplacedItems: number;
  placeableItems: number;
  onSelect: (id: number | null) => void;
  dialog: Dialog | null;
  setDialog: (d: Dialog | null) => void;
}) {
  const navigate = useNavigate();
  const { data: loc, error } = useQuery({ queryKey: ["location", locationId], queryFn: () => locationsApi.get(locationId) });
  const invalidate = useInvalidateLocations();
  const archive = useMutation({
    mutationFn: (archived: boolean) => (archived ? locationsApi.archive(locationId) : locationsApi.unarchive(locationId)),
    onSuccess: () => {
      invalidate();
      setDialog(null);
    },
  });
  const remove = useMutation({
    mutationFn: () => locationsApi.remove(locationId),
    onSuccess: () => {
      invalidate();
      setDialog(null);
      onSelect(null);
    },
  });
  const [moveItemStored, setMoveItemStored] = useState<Awaited<ReturnType<typeof locationsApi.storedIn>> | null>(null);
  const unplace = useMutation({ mutationFn: locationsApi.unplace, onSuccess: invalidate });
  // Counting a place is an ordinary stock take scoped to it. If one is already open for this
  // location the button goes back to it; otherwise it opens the start form with the place chosen.
  const { data: takes } = useQuery({ queryKey: ["stock-takes"], queryFn: stockTakesApi.list });
  const openTake = takes?.find((t) => t.status === "open" && t.location_id === locationId);
  const startCount = () =>
    void (openTake
      ? navigate({ to: "/stock-takes/$stockTakeId", params: { stockTakeId: String(openTake.id) } })
      : navigate({ to: "/stock-takes", search: { location: locationId } }));
  const reduce = useMutation({
    mutationFn: ({ id, qty }: { id: number; qty: string }) => locationsApi.reduce(id, qty),
    onSuccess: invalidate,
  });

  // Moving one item out needs its other placements (for the "now / after" figures), so fetch them
  // when the dialog is asked for.
  useEffect(() => {
    if (dialog?.kind !== "moveItem") {
      setMoveItemStored(null);
      return;
    }
    let cancelled = false;
    const c = dialog.item;
    void locationsApi
      .storedIn({ material_id: c.material_id, product_id: c.product_id, variant_id: c.variant_id })
      .then((s) => !cancelled && setMoveItemStored(s));
    return () => {
      cancelled = true;
    };
  }, [dialog]);

  if (error) return <ErrorBanner error={error} />;
  // Also guards a malformed response, so the pane degrades rather than throwing.
  if (!loc || !Array.isArray(loc.contents)) return <p className="p-4 text-sm text-slate-500">Loading…</p>;

  const drifting = loc.contents.filter((c) => Number(c.drift) > 0);
  const hasStock = loc.contents.length > 0;
  const isEmpty = !hasStock && loc.children.length === 0;
  const canDelete = isEmpty && loc.history_count === 0;

  return (
    <div className="flex flex-col gap-3">
      <div className={`flex flex-col gap-2.5 rounded-lg p-4 shadow-sm ${loc.archived ? "bg-slate-50" : "bg-white"}`}>
        <nav aria-label="Location path" className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-slate-500">
          {loc.path.map((seg, i) => (
            <span key={seg.id} className="flex items-center gap-1.5">
              {i > 0 && <span aria-hidden="true">›</span>}
              {i < loc.path.length - 1 ? (
                <Link to="/locations" search={{ loc: seg.id }} className="text-slate-600 hover:underline">
                  {seg.name}
                </Link>
              ) : (
                <span className="font-medium text-slate-900">{seg.name}</span>
              )}
            </span>
          ))}
        </nav>
        <div className="flex items-center gap-2.5">
          <h2 className="text-base font-semibold">{loc.name}</h2>
          {loc.archived && <Badge className="bg-slate-100 text-slate-600">Archived</Badge>}
          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">{loc.code}</span>
          <CopyButton value={loc.code} label="Copy scan code" />
          <span className="flex-1" />
          <span className="text-xs text-slate-500">
            {loc.children.length > 0 &&
              `${loc.children.length} location${loc.children.length === 1 ? "" : "s"} · `}
            {loc.contents.length} item{loc.contents.length === 1 ? "" : "s"}
          </span>
        </div>
        {loc.archived ? (
          <div className="flex gap-2">
            <button onClick={() => archive.mutate(false)} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
              Unarchive
            </button>
            <span className="self-center text-xs text-slate-500">
              Read-only. Kept so old labels and history still make sense.
            </span>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <ActionButton onClick={() => setDialog({ kind: "add", parentId: loc.id })}>Add child</ActionButton>
            <ActionButton onClick={() => setDialog({ kind: "range", parentId: loc.id })}>Generate range</ActionButton>
            <ActionButton onClick={() => setDialog({ kind: "move" })}>Move location</ActionButton>
            <ActionButton onClick={() => void navigate({ to: "/location-labels", search: { ids: String(loc.id) } })}>
              Print label
            </ActionButton>
            {hasStock && <ActionButton onClick={() => setDialog({ kind: "empty" })}>Empty into…</ActionButton>}
            <ActionButton onClick={startCount}>{openTake ? "Continue count" : "Count"}</ActionButton>
            <span className="flex-1" />
            {canDelete ? (
              <button onClick={() => setDialog({ kind: "delete" })} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-red-700">
                Delete
              </button>
            ) : (
              <button
                onClick={() => setDialog({ kind: "archive" })}
                disabled={!isEmpty}
                title={isEmpty ? undefined : "Move its stock and sub-locations out first"}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Archive
              </button>
            )}
          </div>
        )}
        <ErrorBanner error={archive.error} />
      </div>

      {drifting.map((c) => (
        <div
          key={c.placement_id}
          className="flex items-center gap-3 rounded-lg bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-200"
        >
          <span aria-hidden="true" className="h-[7px] w-[7px] flex-none rounded-full bg-amber-500" />
          <div className="flex-1">
            <b className="font-semibold">{c.name}:</b> more is placed across locations than is in stock (
            {qtyWithUnit(c.drift, c.unit)} too many). This is routine after edits or uncounted use.
          </div>
          <button
            onClick={() => reduce.mutate({ id: c.placement_id, qty: String(Math.min(Number(c.drift), Number(c.qty))) })}
            disabled={reduce.isPending}
            className="rounded-md border border-amber-300 px-2.5 py-1 text-xs"
          >
            Reduce {loc.name} by {qtyWithUnit(Math.min(Number(c.drift), Number(c.qty)), c.unit)}
          </button>
        </div>
      ))}

      {loc.children.length > 0 && <ChildrenTable children={loc.children} onOpen={onSelect} />}

      {hasStock ? (
        <div className="overflow-hidden rounded-lg bg-white shadow-sm">
          <table className="w-full border-collapse text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/60">
                <Th>Item</Th>
                <Th>SKU</Th>
                <Th align="right">Qty</Th>
                <Th>Unit</Th>
                <Th>Primary</Th>
                <Th>{""}</Th>
              </tr>
            </thead>
            <tbody>
              {loc.contents.map((c) => (
                <ContentRow
                  key={c.placement_id}
                  c={c}
                  archived={loc.archived}
                  onMove={() => setDialog({ kind: "moveItem", item: c })}
                  onRemove={() => unplace.mutate(c.placement_id)}
                />
              ))}
            </tbody>
          </table>
          {!loc.archived && (
            <div className="border-t border-slate-100 px-2.5 py-2">
              <button
                onClick={() => setDialog({ kind: "placeHere" })}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs"
              >
                Place an item here
              </button>
            </div>
          )}
        </div>
      ) : (
        loc.children.length === 0 && (
          <EmptyContents
            archived={loc.archived}
            placeableItems={placeableItems}
            unplacedItems={unplacedItems}
            hasLocations={rows.length > 1}
            onPlace={() => setDialog({ kind: "placeHere" })}
          />
        )
      )}
      <ErrorBanner error={unplace.error ?? reduce.error} />

      <History entries={loc.history} total={loc.history_count} />

      {dialog?.kind === "move" && (
        <MoveLocationModal rows={rows} location={loc} onClose={() => setDialog(null)} onMoved={() => setDialog(null)} />
      )}
      {dialog?.kind === "empty" && (
        <EmptyIntoModal rows={rows} location={loc} onClose={() => setDialog(null)} onDone={() => setDialog(null)} />
      )}
      {dialog?.kind === "placeHere" && (
        <PlaceHereModal locationId={loc.id} locationName={loc.name} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "moveItem" && moveItemStored && (
        <PlaceItemModal
          rows={rows}
          item={{
            material_id: dialog.item.material_id,
            product_id: dialog.item.product_id,
            variant_id: dialog.item.variant_id,
          }}
          itemName={dialog.item.name}
          stored={moveItemStored}
          mode="move"
          initialFromId={loc.id}
          onClose={() => setDialog(null)}
        />
      )}
      <ConfirmDialog
        open={dialog?.kind === "archive"}
        title={`Archive ${loc.name}?`}
        body="It stays in the history, and old labels still scan, but it disappears from the pickers. You can unarchive it later."
        confirmLabel="Archive"
        tone="default"
        busy={archive.isPending}
        onConfirm={() => archive.mutate(true)}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "delete"}
        title={`Delete ${loc.name}?`}
        body="It has never held anything, so it can be removed completely."
        confirmLabel="Delete"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
}

function ActionButton({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button onClick={onClick} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
      {children}
    </button>
  );
}

function ChildrenTable({ children, onOpen }: { children: LocationRow[]; onOpen: (id: number) => void }) {
  const sorted = [...children].sort((a, b) => compareNames(a.name, b.name));
  return (
    <div className="overflow-hidden rounded-lg bg-white shadow-sm">
      <table className="w-full border-collapse text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>Inside</Th>
            <Th align="right">Items</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.id} className="border-b border-slate-100">
              <td className="p-2">
                {c.name}
                {c.needs_attention && <Badge className="ml-1.5 bg-amber-100 text-amber-800">Drift</Badge>}
                {c.archived && <Badge className="ml-1.5 bg-slate-100 text-slate-600">Archived</Badge>}
              </td>
              <td className="p-2 text-right tabular-nums">{c.rolled_up_items}</td>
              <td className="p-2 text-right">
                <button onClick={() => onOpen(c.id)} className="text-blue-700 hover:underline">
                  Open
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContentRow({
  c,
  archived,
  onMove,
  onRemove,
}: {
  c: LocationContent;
  archived: boolean;
  onMove: () => void;
  onRemove: () => void;
}) {
  const drift = Number(c.drift) > 0;
  const to = c.material_id != null ? `/materials/${c.material_id}` : c.product_id != null ? `/products/${c.product_id}` : null;
  return (
    <tr className={`border-b border-slate-100 ${drift ? "bg-amber-50" : ""}`}>
      <td className="p-2">
        {to ? (
          <Link to={to as never} className="hover:underline">
            {c.name}
          </Link>
        ) : (
          c.name
        )}
        {drift && <Badge className="ml-1.5 bg-amber-100 text-amber-800">Drift</Badge>}
      </td>
      <td className="p-2 font-mono text-slate-500">{c.sku ?? ""}</td>
      <td className="p-2 text-right tabular-nums">{displayQty(c.qty)}</td>
      <td className="p-2">{c.unit}</td>
      <td className="p-2">
        {c.is_primary ? <Badge className="bg-green-100 text-green-800">Primary</Badge> : <span className="text-slate-500">Overflow</span>}
      </td>
      <td className="p-2 text-right">
        {!archived && (
          <span className="flex justify-end gap-3">
            <button onClick={onMove} className="text-blue-700 hover:underline">
              Move
            </button>
            <button onClick={onRemove} className="text-slate-500 hover:underline" title="Take it out of this location; its stock becomes unassigned">
              Remove
            </button>
          </span>
        )}
      </td>
    </tr>
  );
}

function EmptyContents({
  archived,
  placeableItems,
  unplacedItems,
  hasLocations,
  onPlace,
}: {
  archived: boolean;
  placeableItems: number;
  unplacedItems: number;
  hasLocations: boolean;
  onPlace: () => void;
}) {
  // "Place your stock": locations exist, stock exists, and none of it has been put anywhere.
  if (!archived && hasLocations && placeableItems > 0 && unplacedItems === placeableItems) {
    return (
      <section className="flex flex-col gap-3 rounded-lg bg-amber-50 p-5 ring-1 ring-amber-200">
        <Badge className="self-start bg-amber-100 text-amber-800">{unplacedItems} items unplaced</Badge>
        <h2 className="text-[17px] font-semibold">Place your stock</h2>
        <p className="max-w-[470px] text-sm leading-relaxed text-slate-700">
          You have locations and {unplacedItems} materials and products, but nothing has been put anywhere yet. That’s
          fine. Start with the things you pick most, and place the rest as you go.
        </p>
        <div className="flex flex-wrap gap-2.5">
          <button onClick={onPlace} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
            Place stock item by item
          </button>
          <Link
            to="/materials"
            search={{ location: "unplaced" }}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          >
            Show unplaced items
          </Link>
        </div>
        <p className="text-xs text-slate-500">
          Tip: an item you place in just one location is kept up to date for you from then on.
        </p>
      </section>
    );
  }
  return (
    <section className="flex flex-col items-center gap-2 rounded-lg bg-white p-7 text-center shadow-sm">
      <b className="font-semibold">Nothing stored here yet</b>
      <span className="max-w-[300px] text-[13px] leading-snug text-slate-500">
        Place stock from an item’s “Stored in” section, or move some in from another location.
      </span>
      {!archived && (
        <button onClick={onPlace} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
          Place an item here
        </button>
      )}
    </section>
  );
}

const KIND_LABEL: Record<LocationHistoryEntry["kind"], string> = {
  place: "Placed",
  move: "Moved",
  remove: "Removed",
  auto_deduct: "Taken off automatically",
  auto_add: "Added automatically",
  count: "Counted",
};

function History({ entries, total }: { entries: LocationHistoryEntry[]; total: number }) {
  const [open, setOpen] = useState(false);
  if (total === 0) return null;
  return (
    <section className="rounded-lg bg-white shadow-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px]"
      >
        <span aria-hidden="true" className={`text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>
          ›
        </span>
        <b className="font-medium">History</b>
        <span className="text-slate-500">{total} {total === 1 ? "entry" : "entries"} — moves, automatic deductions and additions</span>
      </button>
      {open && (
        <ul className="divide-y divide-slate-100 border-t border-slate-100 text-[12.5px]">
          {entries.map((e) => (
            <li key={e.id} className="flex items-baseline gap-3 px-3.5 py-2">
              <span className="w-40 flex-none text-slate-500">{new Date(e.created_at).toLocaleString()}</span>
              <span className="flex-1">
                <b className="font-medium">{KIND_LABEL[e.kind]}</b> {displayQty(e.qty)} · {e.item_name}
                {e.from_path && e.to_path && (
                  <span className="text-slate-500">
                    {" "}
                    from {e.from_path} to {e.to_path}
                  </span>
                )}
                {e.from_path && !e.to_path && <span className="text-slate-500"> from {e.from_path}</span>}
                {e.to_path && !e.from_path && <span className="text-slate-500"> into {e.to_path}</span>}
                {e.reason && <span className="text-slate-500"> — {e.reason}</span>}
              </span>
            </li>
          ))}
          {total > entries.length && (
            <li className="px-3.5 py-2 text-xs text-slate-500">Showing the latest {entries.length}.</li>
          )}
        </ul>
      )}
    </section>
  );
}
