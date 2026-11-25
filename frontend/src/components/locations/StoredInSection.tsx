import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { locationsApi, type ItemRef, type StoredIn } from "../../api/locations";
import { qtyWithUnit } from "../../lib/format";
import { last, pathLabel } from "../../lib/locations";
import { Badge } from "../common/Badge";
import { ErrorBanner } from "../common/ErrorBanner";
import { HelpLink } from "../help/HelpLink";
import { PlaceItemModal } from "./MoveModals";
import { useInvalidateLocations, useLocationTree } from "./useLocations";

const SMALL_BTN = "rounded-md border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50";

/**
 * Where an item is kept — one compact card in five states (docs/plan-locations.md §8):
 * not placed, one location, several, some still unassigned, and drift. Sits under the stock
 * figures on a material and on a product or variant.
 *
 * Nothing here is alarming: unplaced stock and drift are routine, so they're amber with a clear
 * next action, never red.
 */
export function StoredInSection({ item, itemName }: { item: ItemRef; itemName: string }) {
  const { data: stored } = useQuery({ queryKey: ["stored-in", item], queryFn: () => locationsApi.storedIn(item) });
  const { data: tree } = useLocationTree();
  const invalidate = useInvalidateLocations();
  const [dialog, setDialog] = useState<null | { mode: "place" | "move"; fromId?: number | null }>(null);

  const makePrimary = useMutation({ mutationFn: locationsApi.makePrimary, onSuccess: invalidate });
  const reduce = useMutation({
    mutationFn: ({ id, qty }: { id: number; qty: string }) => locationsApi.reduce(id, qty),
    onSuccess: invalidate,
  });
  const unplace = useMutation({ mutationFn: locationsApi.unplace, onSuccess: invalidate });

  // Renders nothing until a well-formed answer arrives, so a slow or unexpected response can never
  // get in the way of the material or product page it sits on.
  if (!stored || !Array.isArray(stored.placements)) return null;
  const rows = tree?.locations ?? [];
  const noLocationsYet = rows.filter((r) => !r.archived).length === 0;
  const places = stored.placements;
  const drift = Number(stored.drift);
  const unassigned = Number(stored.unassigned);
  const total = Number(stored.total);

  // The one-click drift fix takes the excess off the primary location if it has enough,
  // otherwise off whichever placement holds the most.
  const fixTarget = drift > 0
    ? [...places].sort((a, b) => Number(b.is_primary) - Number(a.is_primary) || Number(b.qty) - Number(a.qty)).find(
        (p) => Number(p.qty) >= drift,
      ) ?? [...places].sort((a, b) => Number(b.qty) - Number(a.qty))[0]
    : undefined;

  return (
    <section aria-labelledby="stored-in-title" className="flex flex-col gap-2.5 rounded-lg bg-white p-3.5 shadow-sm">
      <div className="flex items-center gap-2">
        <h3 id="stored-in-title" className="text-[13px] font-semibold">
          Stored in
        </h3>
        <HelpLink doc="locations" anchor="placing-and-moving-stock" />
        <span className="flex-1" />
        {places.length === 0 && !noLocationsYet && total > 0 && (
          <button onClick={() => setDialog({ mode: "place" })} className="rounded-md bg-slate-900 px-2.5 py-1 text-xs text-white">
            Place
          </button>
        )}
        {places.length === 1 && (
          <>
            <button onClick={() => setDialog({ mode: "move", fromId: places[0].location_id })} className={SMALL_BTN}>
              Move
            </button>
            {unassigned > 0 && (
              <button onClick={() => setDialog({ mode: "place" })} className={SMALL_BTN}>
                Add another place
              </button>
            )}
          </>
        )}
        {places.length > 1 && (
          <>
            <button onClick={() => setDialog({ mode: "move" })} className={SMALL_BTN}>
              Move
            </button>
            {unassigned > 0 && (
              <button onClick={() => setDialog({ mode: "place" })} className={SMALL_BTN}>
                Place
              </button>
            )}
          </>
        )}
      </div>

      {drift > 0 && fixTarget && (
        <div className="flex items-center gap-2.5 rounded-md bg-amber-50 px-2.5 py-2 text-[12.5px] text-amber-800 ring-1 ring-amber-200">
          <span aria-hidden="true" className="h-[7px] w-[7px] flex-none rounded-full bg-amber-500" />
          <span className="flex-1">
            <b className="font-semibold">
              Placed {qtyWithUnit(stored.placed, stored.unit)}, but only {qtyWithUnit(stored.total, stored.unit)} in stock.
            </b>{" "}
            Something was used without being taken from a location.
          </span>
          <button
            onClick={() => reduce.mutate({ id: fixTarget.id, qty: String(drift) })}
            disabled={reduce.isPending}
            className="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs"
          >
            Reduce {last(fixTarget.location_path)} by {qtyWithUnit(stored.drift, stored.unit)}
          </button>
        </div>
      )}

      {places.length === 0 ? (
        noLocationsYet ? (
          <p className="text-[12.5px] text-slate-500">
            Not stored anywhere yet. <Link to="/locations" className="text-blue-700 hover:underline">Add a location</Link> to
            start keeping track of where things are.
          </p>
        ) : (
          <p className="text-[12.5px] text-slate-500">Not stored anywhere yet</p>
        )
      ) : (
        <PlacementTable
          stored={stored}
          onMakePrimary={(id) => makePrimary.mutate(id)}
          onRemove={(id) => unplace.mutate(id)}
          showUnassigned={places.length > 1 || unassigned > 0}
          onPlace={() => setDialog({ mode: "place" })}
        />
      )}

      {places.length === 1 && drift === 0 && (
        <p
          className="inline-flex items-center gap-1 text-[11.5px] text-green-800"
          title="This item is in one place, so builds, sales and deliveries change this quantity for you. Add a second place and that stops."
        >
          <span aria-hidden="true">↻</span> Updates automatically
        </p>
      )}
      {places.length > 1 && (
        <p className="text-xs text-slate-500">
          <span aria-hidden="true">ⓘ </span>Stock changes for this item aren’t taken from a location automatically. Move
          or count stock to keep these in step.
        </p>
      )}

      <ErrorBanner error={makePrimary.error ?? reduce.error ?? unplace.error} />

      {dialog && (
        <PlaceItemModal
          rows={rows}
          item={item}
          itemName={itemName}
          stored={stored}
          mode={dialog.mode}
          initialFromId={dialog.fromId}
          onClose={() => setDialog(null)}
        />
      )}
    </section>
  );
}

function PlacementTable({
  stored,
  onMakePrimary,
  onRemove,
  showUnassigned,
  onPlace,
}: {
  stored: StoredIn;
  onMakePrimary: (id: number) => void;
  onRemove: (id: number) => void;
  showUnassigned: boolean;
  onPlace: () => void;
}) {
  const single = stored.placements.length === 1;
  const unassigned = Number(stored.unassigned);
  return (
    <table className="w-full border-collapse text-[12.5px]">
      <thead>
        <tr className="border-b border-slate-200 text-left text-[11.5px] font-medium text-slate-500">
          <th className="py-1 pr-2">Location</th>
          <th className="py-1 text-right">Qty</th>
          <th className="w-24 py-1" />
        </tr>
      </thead>
      <tbody>
        {stored.placements.map((p) => (
          <tr key={p.id} className="border-b border-slate-100">
            <td className="py-1.5 pr-2">
              <Link to="/locations" search={{ loc: p.location_id }} className="hover:underline">
                {pathLabel(p.location_path)}
              </Link>
            </td>
            <td className="py-1.5 text-right tabular-nums">{qtyWithUnit(p.qty, stored.unit)}</td>
            <td className="py-1.5 pl-2">
              {p.is_primary ? (
                <Badge className="bg-green-100 text-green-800">Primary</Badge>
              ) : (
                <span className="flex gap-2 text-xs">
                  <button onClick={() => onMakePrimary(p.id)} className="text-blue-700 hover:underline">
                    Make primary
                  </button>
                </span>
              )}
              {!single && !p.is_primary && (
                <button onClick={() => onRemove(p.id)} className="ml-2 text-xs text-slate-500 hover:underline">
                  Remove
                </button>
              )}
            </td>
          </tr>
        ))}
        {showUnassigned && (
          <tr>
            <td className="py-1.5 pr-2 italic text-slate-500">Unassigned</td>
            <td className="py-1.5 text-right tabular-nums text-slate-500">{qtyWithUnit(stored.unassigned, stored.unit)}</td>
            <td className="py-1.5 pl-2">
              {unassigned > 0 && (
                <button onClick={onPlace} className="text-xs text-blue-700 hover:underline">
                  Put away
                </button>
              )}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
