import type { StockTakeLine } from "../api/types";

/**
 * What each line of a stock take does to total stock, which on a count by location is not the
 * difference in the place counted: extra stock found there comes out of the item's Unassigned
 * first, and only the excess over that adds anything (docs/plan-locations.md §7.1).
 *
 * An applied line reports what actually happened. A line still to apply is worked out the same way
 * the server will, sharing each item's Unassigned between its places in the order they apply.
 * Null means there is nothing to say: not counted, or held back.
 */
export function stockChanges(lines: StockTakeLine[], typed: Record<number, string>): Map<number, number | null> {
  const left = new Map<string, number>();
  const out = new Map<number, number | null>();
  for (const line of [...lines].sort((a, b) => a.id - b.id)) {
    const entered = (typed[line.id] ?? "").trim();
    const delta = entered === "" ? null : Number(entered) - Number(line.expected_qty);
    if (line.location_id == null) {
      out.set(line.id, delta);
    } else if (line.status === "applied") {
      out.set(line.id, line.stock_change == null ? null : Number(line.stock_change));
    } else if (delta == null || line.unassigned_qty == null || (line.status !== "pending" && line.status !== "counted")) {
      out.set(line.id, null);
    } else if (delta <= 0) {
      out.set(line.id, delta);
    } else {
      const item = `${line.material_id}/${line.product_id}/${line.variant_id}`;
      const available = left.get(item) ?? Number(line.unassigned_qty);
      const absorbed = Math.min(delta, available);
      left.set(item, available - absorbed);
      out.set(line.id, delta - absorbed);
    }
  }
  return out;
}
