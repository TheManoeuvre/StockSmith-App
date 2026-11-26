import { useQuery } from "@tanstack/react-query";
import { locationsApi, type ItemRef } from "../../api/locations";
import { qtyWithUnit } from "../../lib/format";
import { pathLabel } from "../../lib/locations";

/**
 * "Taking from": which place a manual decrease comes out of, for an item kept in several.
 *
 * Shown only when it matters: the item has two or more places and the adjustment is a decrease.
 * Left on "Primary place first" it changes nothing from the default behaviour, so it is never a
 * required field and never a blocking dialog (docs/plan-locations.md §5.2).
 */
export function TakingFromSelect({
  item,
  decreasing,
  value,
  onChange,
}: {
  item: ItemRef;
  decreasing: boolean;
  value: number | null;
  onChange: (locationId: number | null) => void;
}) {
  const { data: stored } = useQuery({ queryKey: ["stored-in", item], queryFn: () => locationsApi.storedIn(item) });
  if (!decreasing || !stored || !Array.isArray(stored.placements) || stored.placements.length < 2) return null;
  const places = stored.placements.filter((p) => Number(p.qty) > 0);
  if (places.length < 2) return null;
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm">Taking from</span>
      <select
        className="rounded border border-slate-300 px-2 py-1"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      >
        <option value="">Primary place first</option>
        {places.map((p) => (
          <option key={p.location_id} value={p.location_id}>
            {pathLabel(p.location_path)} ({qtyWithUnit(p.qty, stored.unit)})
          </option>
        ))}
      </select>
    </label>
  );
}
