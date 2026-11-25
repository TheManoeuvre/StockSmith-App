import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { locationsApi, type ItemLocationSummary, type LocationRow } from "../../api/locations";
import { stockTakesApi } from "../../api/stockTakes";
import { qtyWithUnit, wholeNumberStepFor } from "../../lib/format";
import { subtreeIds } from "../../lib/locations";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LocationPicker } from "./LocationPicker";

/**
 * "Add an item found here": something is on the shelf that the sheet doesn't list. It joins the
 * stock take as a line with nothing expected, and is placed when the take is approved.
 */
export function AddFoundItemModal({
  stockTakeId,
  rootId,
  rows,
  onClose,
  onAdded,
}: {
  stockTakeId: number;
  /** The location being counted; the item can be put in it or anywhere inside it. */
  rootId: number;
  rows: LocationRow[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const { data: items } = useQuery({ queryKey: ["location-summaries"], queryFn: locationsApi.summaries });
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<ItemLocationSummary | null>(null);
  const [locationId, setLocationId] = useState<number>(rootId);
  const [qty, setQty] = useState("");

  const inside = useMemo(() => new Set(subtreeIds(rows, rootId)), [rows, rootId]);
  const blocked = useMemo(() => {
    const m = new Map<number, string>();
    for (const r of rows) {
      if (r.archived) m.set(r.id, "archived");
      else if (!inside.has(r.id)) m.set(r.id, "outside this stock take");
    }
    return m;
  }, [rows, inside]);

  const matches = useMemo(
    () =>
      (items ?? [])
        .filter((i) => i.name.toLowerCase().includes(query.trim().toLowerCase()))
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
        .slice(0, 50),
    [items, query],
  );

  const add = useMutation({
    mutationFn: () =>
      stockTakesApi.addFound(
        stockTakeId,
        { material_id: chosen!.material_id, product_id: chosen!.product_id, variant_id: chosen!.variant_id },
        locationId,
        qty,
      ),
    onSuccess: onAdded,
  });
  const n = Number(qty);
  const valid = chosen != null && qty !== "" && Number.isFinite(n) && n >= 0;
  const unassigned = chosen ? Math.max(Number(chosen.total) - Number(chosen.placed), 0) : 0;

  return (
    <Modal
      title="Add an item found here"
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            onClick={() => add.mutate()}
            disabled={!valid || add.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add to the sheet
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {!chosen ? (
          <>
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search materials and products"
              aria-label="Search items"
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
            <div className="flex max-h-64 flex-col overflow-y-auto rounded-md border border-slate-200">
              {matches.length === 0 ? (
                <p className="p-3 text-xs text-slate-500">{items ? "No item matches that." : "Loading…"}</p>
              ) : (
                matches.map((item) => (
                  <button
                    key={`${item.material_id}-${item.product_id}-${item.variant_id}`}
                    onClick={() => setChosen(item)}
                    className="flex items-center justify-between gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left text-[13px] last:border-b-0 hover:bg-slate-50"
                  >
                    <span className="truncate">{item.name}</span>
                    <span className="flex-none text-xs tabular-nums text-slate-500">
                      {qtyWithUnit(item.total, item.unit)} in stock
                    </span>
                  </button>
                ))
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center justify-between rounded-md bg-slate-50 px-2.5 py-2 text-sm">
              <span className="font-medium">{chosen.name}</span>
              <button onClick={() => setChosen(null)} className="text-xs text-blue-700 hover:underline">
                Change
              </button>
            </div>
            <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
              Where
              <LocationPicker rows={rows} value={locationId} onChange={(id) => id != null && setLocationId(id)} disabled={blocked} label="Where it was found" />
            </div>
            <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
              How many are there
              <input
                type="number"
                min="0"
                step={wholeNumberStepFor(chosen.unit)}
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                className="w-28 rounded border border-slate-300 px-2 py-1.5 text-right text-sm font-normal tabular-nums"
              />
            </label>
            <p className="rounded-md bg-slate-50 px-2.5 py-2 text-[12.5px] leading-snug text-slate-600">
              {unassigned > 0
                ? `${qtyWithUnit(unassigned, chosen.unit)} of ${chosen.name} isn’t placed anywhere yet, so what you found here is taken from that first. Only anything beyond it adds to your stock.`
                : `Everything of ${chosen.name} is already placed, so what you found here adds to your stock when the take is approved.`}
            </p>
          </>
        )}
        {add.error && <ErrorBanner error={add.error} />}
      </div>
    </Modal>
  );
}
