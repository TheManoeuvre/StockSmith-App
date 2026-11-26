import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { createFileRoute, Link } from "@tanstack/react-router";
import type { LocationRow } from "../api/locations";
import { LabelSizeControl } from "../components/locations/LabelSizeControl";
import { LocationLabel } from "../components/locations/LocationLabel";
import { useLocationTree } from "../components/locations/useLocations";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { HelpLink } from "../components/help/HelpLink";
import { describeSize, useLabelSize } from "../lib/labels";
import { buildTree, subtreeIds, type TreeNode } from "../lib/locations";

/**
 * Print location labels (docs/plan-locations.md §6): tick locations, pick a size, check the
 * preview, print. Reached from the Locations page, from a location's own "Print label", and from
 * "Print labels for these" straight after generating a range.
 *
 * `?ids=` preselects locations (comma-separated) so those entry points can say which.
 */
export const Route = createFileRoute("/location-labels")({
  validateSearch: (search: Record<string, unknown>): { ids?: string } => {
    const raw = typeof search.ids === "string" || typeof search.ids === "number" ? String(search.ids) : "";
    return /^\d+(,\d+)*$/.test(raw) ? { ids: raw } : {};
  },
  component: LocationLabelsPage,
});

interface FlatRow {
  row: LocationRow;
  depth: number;
}

function flatten(nodes: TreeNode[]): FlatRow[] {
  const out: FlatRow[] = [];
  const walk = (n: TreeNode) => {
    out.push({ row: n.row, depth: n.depth });
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return out;
}

function LocationLabelsPage() {
  const { ids } = Route.useSearch();
  const { data: tree, error } = useLocationTree();
  const [size, setSize] = useLabelSize();
  const [selected, setSelected] = useState<Set<number>>(() => new Set((ids ?? "").split(",").filter(Boolean).map(Number)));
  const [beneath, setBeneath] = useState(false);
  // Sheet printing lays several labels out on A4 for sticky label sheets; one-per-page is for roll printers.
  const [onSheet, setOnSheet] = useState(false);

  const rows = useMemo(() => tree?.locations ?? [], [tree]);
  const flat = useMemo(() => flatten(buildTree(rows, false)), [rows]);

  // What will actually print: the ticked locations, plus everything beneath each when asked.
  const printing = useMemo(() => {
    const chosen = new Set<number>();
    for (const id of selected) {
      if (beneath) for (const d of subtreeIds(rows, id)) chosen.add(d);
      else chosen.add(id);
    }
    const byId = new Map(rows.map((r) => [r.id, r]));
    return flat
      .filter((f) => chosen.has(f.row.id) && !f.row.archived)
      .map((f) => {
        const ancestors: string[] = [];
        let cur = f.row.parent_id != null ? byId.get(f.row.parent_id) : undefined;
        while (cur) {
          ancestors.unshift(cur.name);
          cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined;
        }
        return { id: f.row.id, name: f.row.name, code: f.row.code, ancestors };
      });
  }, [selected, beneath, rows, flat]);

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // A4 with a 10 mm margin all round. Labels bigger than half a sheet can't be tiled usefully.
  const sheetFits = size.widthMm <= 95 && size.heightMm <= 138;
  const sheet = onSheet && sheetFits;
  const perRow = Math.max(1, Math.floor(190 / size.widthMm));
  const perPage = perRow * Math.max(1, Math.floor(277 / size.heightMm));
  const pages = useMemo(() => {
    const out: (typeof printing)[] = [];
    for (let i = 0; i < printing.length; i += perPage) out.push(printing.slice(i, i + perPage));
    return out;
  }, [printing, perPage]);
  const large = size.widthMm >= 100;
  const previewMm = large ? "3px" : "6px";
  const count = printing.length;

  return (
    <div className="flex flex-col gap-3">
      <header className="flex items-center gap-2.5 print:hidden">
        <Link to="/locations" className="text-sm text-slate-500 hover:underline">
          ← Locations
        </Link>
        <h1 className="text-xl font-semibold">Print labels</h1>
        <HelpLink doc="locations" anchor="printing-labels" />
      </header>
      <ErrorBanner error={error} />

      <section className="flex min-h-[430px] overflow-hidden rounded-lg bg-white shadow-sm print:hidden">
        <div className="flex w-[330px] flex-none flex-col gap-3 border-r border-slate-200 p-4">
          <LabelSizeControl size={size} onChange={setSize} />

          <div className="text-xs font-medium text-slate-700">Locations to print</div>
          <div className="max-h-[360px] min-h-[120px] flex-1 overflow-y-auto rounded-md border border-slate-200 px-2 py-1.5">
            {flat.length === 0 ? (
              <p className="py-2 text-xs text-slate-500">
                No locations yet. <Link to="/locations" className="text-blue-700 hover:underline">Add some first.</Link>
              </p>
            ) : (
              flat.map(({ row, depth }) => (
                <label
                  key={row.id}
                  className="flex h-[25px] cursor-pointer items-center gap-2 text-[13px] text-slate-700"
                  style={{ paddingLeft: depth * 20 }}
                >
                  <input type="checkbox" checked={selected.has(row.id)} onChange={() => toggle(row.id)} />
                  <span className={depth === 0 ? "font-semibold" : ""}>{row.name}</span>
                </label>
              ))
            )}
          </div>
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" checked={beneath} onChange={(e) => setBeneath(e.target.checked)} />
            Include everything beneath each tick
          </label>
          <label className={`flex items-center gap-2 text-[12.5px] ${sheetFits ? "" : "text-slate-400"}`}>
            <input type="checkbox" checked={sheet} disabled={!sheetFits} onChange={(e) => setOnSheet(e.target.checked)} />
            Several to a page on A4 (label sheets)
          </label>
          <div className="flex items-center gap-2">
            <span className="flex-1 text-[12.5px] text-slate-500">
              {count} label{count === 1 ? "" : "s"} · {sheet ? `${perPage} per A4 page` : "one per page"}
            </span>
            <button
              onClick={() => window.print()}
              disabled={count === 0}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Print {count} label{count === 1 ? "" : "s"}
            </button>
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-3 bg-slate-200 p-5">
          <p className="text-xs text-slate-500">
            {count === 0
              ? "Tick a location to see its label."
              : sheet
                ? `Preview — ${count} label${count === 1 ? "" : "s"} across ${pages.length} A4 page${pages.length === 1 ? "" : "s"}, ${perRow} across. Print at 100% scale, not “fit to page”.`
                : `Preview — ${count} label${count === 1 ? "" : "s"}. Your printer’s paper size should be set to ${describeSize(size)}.`}
          </p>
          <div className="flex flex-wrap gap-4">
            {printing.slice(0, 12).map((l) => (
              <div key={l.id} className="flex-none border border-slate-500 shadow-md">
                <LocationLabel ancestors={l.ancestors} name={l.name} code={l.code} size={size} mm={previewMm} />
              </div>
            ))}
          </div>
          {count > 12 && <p className="text-xs text-slate-500">…and {count - 12} more, each on its own page.</p>}
        </div>
      </section>

      {/* The real thing: rendered into <body>, hidden on screen, and the only thing a print shows.
          One label per page at the chosen size. */}
      {createPortal(
        <div className="location-label-print" aria-hidden="true">
          <style>{`
            .location-label-print { display: none; }
            @media print {
              @page { size: ${sheet ? "A4" : `${size.widthMm}mm ${size.heightMm}mm`}; margin: ${sheet ? "10mm" : "0"}; }
              html, body { margin: 0 !important; padding: 0 !important; background: #fff !important; }
              body > *:not(.location-label-print) { display: none !important; }
              .location-label-print { display: block; }
              .location-label-print .label-page {
                width: ${size.widthMm}mm; height: ${size.heightMm}mm;
                overflow: hidden; break-after: page; page-break-after: always;
              }
              .location-label-print .label-page:last-child { break-after: auto; page-break-after: auto; }
              .location-label-print .label-sheet {
                display: flex; flex-wrap: wrap; align-content: flex-start; width: ${perRow * size.widthMm}mm;
                break-after: page; page-break-after: always;
              }
              .location-label-print .label-sheet:last-child { break-after: auto; page-break-after: auto; }
              .location-label-print .label-sheet .label-cell {
                width: ${size.widthMm}mm; height: ${size.heightMm}mm; overflow: hidden; flex: none;
              }
            }
          `}</style>
          {sheet
            ? pages.map((page, n) => (
                <div key={n} className="label-sheet">
                  {page.map((l) => (
                    <div key={l.id} className="label-cell">
                      <LocationLabel ancestors={l.ancestors} name={l.name} code={l.code} size={size} mm="1mm" />
                    </div>
                  ))}
                </div>
              ))
            : printing.map((l) => (
                <div key={l.id} className="label-page">
                  <LocationLabel ancestors={l.ancestors} name={l.name} code={l.code} size={size} mm="1mm" />
                </div>
              ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
