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
}

/**
 * Asks where new stock went, for the items that can't work it out themselves.
 *
 * An item kept in exactly one place follows its stock automatically, so it is never asked about.
 * What is left is stock that arrived for an item with no place yet, or with several: it sits
 * Unassigned until someone says where. The primary place is pre-filled for the several case
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
  const answers = useQueries({
    queries: items.map((i) => ({ queryKey: ["stored-in", i.ref], queryFn: () => locationsApi.storedIn(i.ref) })),
  });
  const loaded = answers.every((a) => a.data);
  const rows = items
    .map((item, n) => ({ item, stored: answers[n].data as StoredIn | undefined }))
    .filter((r): r is { item: PutAwayItem; stored: StoredIn } =>
      Boolean(r.stored && Array.isArray(r.stored.placements) && Number(r.stored.unassigned) > 0 && r.stored.placements.length !== 1),
    );
  const noLocations = !tree || tree.locations.filter((l) => !l.archived).length === 0;
  const nothingToAsk = loaded && (rows.length === 0 || noLocations);

  useEffect(() => {
    if (nothingToAsk) onClose();
  }, [nothingToAsk, onClose]);

  const [chosen, setChosen] = useState<Record<number, number | null>>({});
  const [qtys, setQtys] = useState<Record<number, string>>({});
  const put = useMutation({
    mutationFn: async () => {
      for (const [n, r] of rows.entries()) {
        const primary = r.stored.placements.find((p) => p.is_primary)?.location_id ?? null;
        const where = n in chosen ? chosen[n] : primary;
        if (where == null) continue;
        await locationsApi.place(r.item.ref, where, qtys[n] ?? r.stored.unassigned);
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
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
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
                <span className="text-slate-500">· {qtyWithUnit(r.stored.unassigned, r.stored.unit)} not placed yet</span>
              </p>
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <LocationPicker
                    rows={tree?.locations ?? []}
                    value={value}
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
                  value={qtys[n] ?? displayQty(r.stored.unassigned)}
                  onChange={(e) => setQtys((q) => ({ ...q, [n]: e.target.value }))}
                  className="w-24 rounded-md border border-slate-300 px-2 py-1.5 text-right text-sm tabular-nums"
                />
              </div>
            </li>
          );
        })}
      </ul>
      <ErrorBanner error={put.error} />
    </Modal>
  );
}
