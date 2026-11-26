import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  locationsApi,
  type ItemRef,
  type LocationDetail,
  type LocationRow,
  type StoredIn,
} from "../../api/locations";
import { displayQty, qtyWithUnit, wholeNumberStepFor } from "../../lib/format";
import { last, pathLabel, pathNames, subtreeIds } from "../../lib/locations";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LocationPicker } from "./LocationPicker";
import { useInvalidateLocations } from "./useLocations";

const BTN = "rounded-md border border-slate-300 px-3 py-1.5 text-sm";
const PRIMARY =
  "rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50";

function KindTag({ children }: { children: string }) {
  return (
    <span className="mb-2 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-wider text-slate-600">
      {children}
    </span>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="rounded-md bg-slate-50 px-2.5 py-2 text-[12.5px] leading-snug text-slate-600">{children}</p>;
}

/** Re-parents one location. Its contents travel with it, because stock points at the location. */
export function MoveLocationModal({
  rows,
  location,
  onClose,
  onMoved,
}: {
  rows: LocationRow[];
  location: LocationDetail;
  onClose: () => void;
  onMoved: () => void;
}) {
  const [parentId, setParentId] = useState<number | null>(location.parent_id);
  const invalidate = useInvalidateLocations();
  const move = useMutation({
    mutationFn: () => locationsApi.update(location.id, { parent_id: parentId }),
    onSuccess: () => {
      invalidate();
      onMoved();
    },
  });
  const blocked = useMemo(() => {
    const m = new Map<number, string>();
    for (const id of subtreeIds(rows, location.id)) {
      m.set(id, id === location.id ? "can’t move into itself" : `it’s inside ${location.name}`);
    }
    for (const r of rows) if (r.archived) m.set(r.id, "archived");
    return m;
  }, [rows, location]);

  const before = pathLabel(pathNames(rows, location.id));
  const afterParent = parentId != null ? pathNames(rows, parentId) : [];
  const after = pathLabel([...afterParent, location.name]);
  const items = rows.find((r) => r.id === location.id)?.rolled_up_items ?? 0;
  const unchanged = parentId === location.parent_id;

  return (
    <Modal
      title={`Move ${location.name}`}
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className={BTN}>
            Cancel
          </button>
          <button onClick={() => move.mutate()} disabled={unchanged || move.isPending} className={PRIMARY}>
            Move {location.name}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <KindTag>Moves the location</KindTag>
        <div className="flex flex-col gap-0.5 rounded-md bg-slate-50 px-2.5 py-2 font-mono text-xs">
          <span className="text-slate-500">Now&nbsp;&nbsp;{before}</span>
          <span className="text-slate-900">After {after}</span>
        </div>
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          New parent
          <LocationPicker
            rows={rows}
            value={parentId}
            onChange={setParentId}
            allowTopLevel
            disabled={blocked}
            label="New parent"
          />
        </div>
        <Note>
          {location.name} and the <b className="font-semibold">{items} item{items === 1 ? "" : "s"}</b> in it move
          together. Item totals don’t change.
        </Note>
        <ErrorBanner error={move.error} />
      </div>
    </Modal>
  );
}

/** Takes everything out of one location and merges it into another. The source stays, empty. */
export function EmptyIntoModal({
  rows,
  location,
  onClose,
  onDone,
}: {
  rows: LocationRow[];
  location: LocationDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const [toId, setToId] = useState<number | null>(null);
  const invalidate = useInvalidateLocations();
  const move = useMutation({
    mutationFn: () => locationsApi.emptyInto(location.id, toId!),
    onSuccess: () => {
      invalidate();
      onDone();
    },
  });
  const blocked = useMemo(() => {
    const m = new Map<number, string>([[location.id, "that’s the one being emptied"]]);
    for (const r of rows) if (r.archived) m.set(r.id, "archived");
    return m;
  }, [rows, location.id]);
  const count = location.contents.length;
  const toName = toId != null ? last(pathNames(rows, toId)) : null;

  return (
    <Modal
      title={`Empty ${location.name} into…`}
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className={BTN}>
            Cancel
          </button>
          <button onClick={() => move.mutate()} disabled={toId == null || count === 0 || move.isPending} className={PRIMARY}>
            {toName ? `Move ${count} item${count === 1 ? "" : "s"} to ${toName}` : "Choose where to move them"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <KindTag>Moves the contents</KindTag>
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          Into
          <LocationPicker rows={rows} value={toId} onChange={setToId} disabled={blocked} label="Move everything into" />
        </div>
        {count > 0 && (
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-200 text-left text-[11.5px] font-medium text-slate-500">
                <th className="py-1 pr-2">Item</th>
                <th className="py-1 text-right">Moving</th>
              </tr>
            </thead>
            <tbody>
              {location.contents.slice(0, 5).map((c) => (
                <tr key={c.placement_id} className="border-b border-slate-100">
                  <td className="py-1.5 pr-2">{c.name}</td>
                  <td className="py-1.5 text-right tabular-nums">{qtyWithUnit(c.qty, c.unit)}</td>
                </tr>
              ))}
              {count > 5 && (
                <tr>
                  <td colSpan={2} className="py-1.5 text-slate-500">
                    + {count - 5} more item{count - 5 === 1 ? "" : "s"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
        <Note>
          {count} item{count === 1 ? "" : "s"} move{count === 1 ? "s" : ""}. {location.name} stays, empty, so you can
          reuse or archive it. Item totals don’t change.
        </Note>
        <ErrorBanner error={move.error} />
      </div>
    </Modal>
  );
}

/**
 * Place stock for the first time, or move some between two locations. One dialog for both: the
 * difference is whether there is a "from".
 */
export function PlaceItemModal({
  rows,
  item,
  itemName,
  stored,
  mode,
  initialFromId,
  initialToId,
  initialQty,
  onClose,
}: {
  rows: LocationRow[];
  item: ItemRef;
  itemName: string;
  stored: StoredIn;
  mode: "place" | "move";
  initialFromId?: number | null;
  initialToId?: number | null;
  initialQty?: string;
  onClose: () => void;
}) {
  const places = stored.placements;
  const [fromId, setFromId] = useState<number | null>(initialFromId ?? places[0]?.location_id ?? null);
  const [toId, setToId] = useState<number | null>(initialToId ?? null);
  const from = places.find((p) => p.location_id === fromId);
  const available = mode === "place" ? Number(stored.unassigned) : Number(from?.qty ?? 0);
  const [qty, setQty] = useState(initialQty ?? (mode === "place" ? displayQty(stored.unassigned) : ""));
  const invalidate = useInvalidateLocations();
  const run = useMutation({
    mutationFn: () =>
      mode === "place"
        ? locationsApi.place(item, toId!, qty).then(() => undefined)
        : locationsApi.moveItem(item, fromId!, toId!, qty),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });
  const blocked = useMemo(() => {
    const m = new Map<number, string>();
    for (const r of rows) if (r.archived) m.set(r.id, "archived");
    if (mode === "move" && fromId != null) m.set(fromId, "where it’s moving from");
    return m;
  }, [rows, mode, fromId]);

  const n = Number(qty);
  const valid = Number.isFinite(n) && n > 0 && n <= available && toId != null && (mode === "place" || fromId != null);
  const dest = places.find((p) => p.location_id === toId);
  const step = wholeNumberStepFor(stored.unit);

  return (
    <Modal
      title={mode === "place" ? `Place ${itemName}` : `Move ${itemName}`}
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className={BTN}>
            Cancel
          </button>
          <button onClick={() => run.mutate()} disabled={!valid || run.isPending} className={PRIMARY}>
            {mode === "place" ? "Place" : "Move"} {valid ? qtyWithUnit(qty, stored.unit) : ""}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <KindTag>{mode === "place" ? "Puts stock in a location" : "Moves some stock"}</KindTag>
        {mode === "move" && (
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            From
            <select
              value={fromId ?? ""}
              onChange={(e) => setFromId(Number(e.target.value))}
              className="rounded border border-slate-300 px-2 py-1.5 text-sm font-normal"
            >
              {places.map((p) => (
                <option key={p.id} value={p.location_id}>
                  {pathLabel(p.location_path)} ({qtyWithUnit(p.qty, stored.unit)})
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          {mode === "place" ? "Location" : "To"}
          <LocationPicker rows={rows} value={toId} onChange={setToId} disabled={blocked} label="Destination" />
        </div>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          Quantity {stored.unit && stored.unit !== "each" ? `(${stored.unit})` : ""}
          <div className="flex items-center gap-2">
            <input
              type="number"
              min="0"
              step={step}
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
        {mode === "place" ? (
          <Note>
            {qtyWithUnit(stored.unassigned, stored.unit)} of {itemName} isn’t placed anywhere yet.
            {places.length === 0 && " An item you keep in just one place is kept up to date for you from then on."}
          </Note>
        ) : (
          from && (
            <Note>
              Stock moves; the total stays {qtyWithUnit(stored.total, stored.unit)}.
              {valid &&
                ` ${last(pathNames(rows, from.location_id))} will have ${qtyWithUnit(Number(from.qty) - n, stored.unit)}` +
                  `${dest ? `, ${last(pathNames(rows, dest.location_id))} ${qtyWithUnit(Number(dest.qty) + n, stored.unit)}` : ""}.`}
            </Note>
          )
        )}
        <ErrorBanner error={run.error} />
      </div>
    </Modal>
  );
}
