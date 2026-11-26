import { useEffect, useState } from "react";
import { useMutation, useQueries } from "@tanstack/react-query";
import { locationsApi, type ItemRef, type StoredIn } from "../../api/locations";
import { displayQty, qtyWithUnit } from "../../lib/format";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LocationPicker } from "./LocationPicker";
import { useInvalidateLocations, useLocationTree } from "./useLocations";

export interface PutAwayItem {
  ref: ItemRef;
  name: string;
  /** How much just arrived or was built. The suggestion is this, not all the unplaced stock. */
  qty: number;
}

/** What arrived, but never more than is actually unplaced. */
function suggestedQty(item: PutAwayItem, stored: StoredIn): string {
  return Number(stored.unassigned) <= item.qty ? stored.unassigned : String(item.qty);
}

/**
 * Asks where new stock went, for the items that can't work it out themselves.
 *
 * An item kept in exactly one place follows its stock automatically, so it is never asked about.
 * An item kept in several is asked about, with the primary place pre-filled. An item with no place
 * is asked about too, so it can be given a first home, unless someone has said not to for it
 * (docs/plan-locations.md §5.2). Closes itself when there is nothing to ask.
 */
export function PutAwayModal({
  title,
  items,
  onClose,
}: {
  title: string;
  items: PutAwayItem[];
  onClose: () => void;
}) {
  const { data: tree } = useLocationTree();
  const invalidate = useInvalidateLocations();
  // Opened straight after stock changed, so a cached answer is the one from before the change:
  // wait for a fresh one rather than deciding there is nothing to ask.
  const answers = useQueries({
    queries: items.map((i) => ({
      queryKey: ["stored-in", i.ref],
      queryFn: () => locationsApi.storedIn(i.ref),
      staleTime: 0,
      refetchOnMount: "always" as const,
    })),
  });
  const loaded = Boolean(tree) && answers.every((a) => a.isFetchedAfterMount && a.data);
  const rows = items
    .map((item, n) => ({ item, stored: answers[n].data as StoredIn | undefined }))
    .filter((r): r is { item: PutAwayItem; stored: StoredIn } =>
      Boolean(
        r.stored &&
          Array.isArray(r.stored.placements) &&
          Number(r.stored.unassigned) > 0 &&
          (r.stored.placements.length > 1 || (r.stored.placements.length === 0 && r.stored.ask_to_put_away !== false)),
      ),
    );
  const noLocations = !tree || tree.locations.filter((l) => !l.archived).length === 0;
  const nothingToAsk = loaded && (rows.length === 0 || noLocations);

  useEffect(() => {
    if (nothingToAsk) onClose();
  }, [nothingToAsk, onClose]);

  const [chosen, setChosen] = useState<Record<number, number | null>>({});
  const [qtys, setQtys] = useState<Record<number, string>>({});
  const [never, setNever] = useState<Record<number, boolean>>({});
  const saveNever = async () => {
    for (const [n, r] of rows.entries()) {
      if (never[n]) await locationsApi.setAskToPutAway(r.item.ref, false);
    }
  };
  // "Not now", Escape and the close button all keep a ticked "don't ask": it was a clear answer.
  const dismiss = useMutation({
    mutationFn: saveNever,
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });
  const close = () => (Object.values(never).some(Boolean) ? dismiss.mutate() : onClose());
  const put = useMutation({
    mutationFn: async () => {
      await saveNever();
      for (const [n, r] of rows.entries()) {
        if (never[n]) continue;
        const primary = r.stored.placements.find((p) => p.is_primary)?.location_id ?? null;
        const where = n in chosen ? chosen[n] : primary;
        if (where == null) continue;
        await locationsApi.place(r.item.ref, where, qtys[n] ?? suggestedQty(r.item, r.stored));
      }
    },
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  if (nothingToAsk || !loaded) return null;

  return (
    <Modal
      title={title}
      subtitle="Choose where each of these went. Leave one as it is to put it away later."
      maxWidth="max-w-xl"
      onClose={close}
      footer={
        <>
          <button onClick={close} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Not now
          </button>
          <button
            onClick={() => put.mutate()}
            disabled={put.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            Put away
          </button>
        </>
      }
    >
      <ul className="flex flex-col gap-3">
        {rows.map((r, n) => {
          const primary = r.stored.placements.find((p) => p.is_primary)?.location_id ?? null;
          const value = n in chosen ? chosen[n] : primary;
          return (
            <li key={n} className="flex flex-col gap-1.5 border-b border-slate-100 pb-3 last:border-0">
              <p className="text-sm">
                <b className="font-medium">{r.item.name}</b>{" "}
                <span className="text-slate-500">
                  ·{" "}
                  {Number(suggestedQty(r.item, r.stored)) < Number(r.stored.unassigned)
                    ? `${qtyWithUnit(String(r.item.qty), r.stored.unit)} just added · `
                    : ""}
                  {qtyWithUnit(r.stored.unassigned, r.stored.unit)} not placed yet
                </span>
              </p>
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <LocationPicker
                    rows={tree?.locations ?? []}
                    value={never[n] ? null : value}
                    onChange={(id) => setChosen((c) => ({ ...c, [n]: id }))}
                    disabled={new Map((tree?.locations ?? []).filter((l) => l.archived).map((l) => [l.id, "archived"]))}
                    placeholder="Choose a location"
                    label={`Where ${r.item.name} went`}
                  />
                </div>
                <input
                  aria-label={`How much of ${r.item.name}`}
                  type="number"
                  min="0"
                  max={Number(r.stored.unassigned)}
                  step="any"
                  disabled={never[n]}
                  value={qtys[n] ?? displayQty(suggestedQty(r.item, r.stored))}
                  onChange={(e) => setQtys((q) => ({ ...q, [n]: e.target.value }))}
                  className="w-24 rounded-md border border-slate-300 px-2 py-1.5 text-right text-sm tabular-nums"
                />
              </div>
              {/* Only for items with no place: one kept in several places is clearly being tracked. */}
              {r.stored.placements.length === 0 && (
                <label className="flex items-center gap-2 text-xs text-slate-500">
                  <input
                    type="checkbox"
                    checked={never[n] ?? false}
                    onChange={(e) => setNever((v) => ({ ...v, [n]: e.target.checked }))}
                  />
                  Don’t ask about {r.item.name} again
                </label>
              )}
            </li>
          );
        })}
      </ul>
      <ErrorBanner error={put.error ?? dismiss.error} />
    </Modal>
  );
}
