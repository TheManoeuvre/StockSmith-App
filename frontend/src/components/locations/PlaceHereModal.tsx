import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { locationsApi, type ItemLocationSummary } from "../../api/locations";
import { displayQty, qtyWithUnit, wholeNumberStepFor } from "../../lib/format";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { useInvalidateLocations } from "./useLocations";

/**
 * "Place an item here": pick something that still has stock to place, say how much, and it goes
 * into this location. The other way in is the item's own "Stored in" section.
 */
export function PlaceHereModal({
  locationId,
  locationName,
  onClose,
}: {
  locationId: number;
  locationName: string;
  onClose: () => void;
}) {
  const { data: items } = useQuery({ queryKey: ["location-summaries"], queryFn: locationsApi.summaries });
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<ItemLocationSummary | null>(null);
  const [qty, setQty] = useState("");
  const invalidate = useInvalidateLocations();

  const placeable = useMemo(
    () =>
      (items ?? [])
        .map((i) => ({ item: i, unassigned: Math.max(Number(i.total) - Number(i.placed), 0) }))
        .filter((r) => r.unassigned > 0)
        .filter((r) => r.item.name.toLowerCase().includes(query.trim().toLowerCase()))
        .sort((a, b) => a.item.name.localeCompare(b.item.name, undefined, { numeric: true })),
    [items, query],
  );

  const place = useMutation({
    mutationFn: () =>
      locationsApi.place(
        { material_id: chosen!.material_id, product_id: chosen!.product_id, variant_id: chosen!.variant_id },
        locationId,
        qty,
      ),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  const available = chosen ? Math.max(Number(chosen.total) - Number(chosen.placed), 0) : 0;
  const n = Number(qty);
  const valid = chosen != null && Number.isFinite(n) && n > 0 && n <= available;

  return (
    <Modal
      title={`Place an item in ${locationName}`}
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            onClick={() => place.mutate()}
            disabled={!valid || place.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Place {valid ? qtyWithUnit(qty, chosen!.unit) : ""}
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
            <div className="flex max-h-72 flex-col overflow-y-auto rounded-md border border-slate-200">
              {placeable.length === 0 ? (
                <p className="p-3 text-xs text-slate-500">
                  {items ? "Nothing left to place: every item with stock is already placed." : "Loading…"}
                </p>
              ) : (
                placeable.map(({ item, unassigned }) => (
                  <button
                    key={`${item.material_id}-${item.product_id}-${item.variant_id}`}
                    onClick={() => {
                      setChosen(item);
                      setQty(displayQty(unassigned));
                    }}
                    className="flex items-center justify-between gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left text-[13px] last:border-b-0 hover:bg-slate-50"
                  >
                    <span className="truncate">{item.name}</span>
                    <span className="flex-none text-xs tabular-nums text-slate-500">
                      {qtyWithUnit(unassigned, item.unit)} to place
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
            <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
              Quantity
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min="0"
                  step={wholeNumberStepFor(chosen.unit)}
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  className="w-28 rounded border border-slate-300 px-2 py-1.5 text-right text-sm font-normal tabular-nums"
                />
                <button
                  type="button"
                  onClick={() => setQty(displayQty(available))}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs font-normal"
                >
                  All {displayQty(available)}
                </button>
              </div>
            </label>
            <p className="rounded-md bg-slate-50 px-2.5 py-2 text-[12.5px] text-slate-600">
              {qtyWithUnit(available, chosen.unit)} of {chosen.name} isn’t placed anywhere yet.
            </p>
          </>
        )}
        <ErrorBanner error={place.error} />
      </div>
    </Modal>
  );
}
