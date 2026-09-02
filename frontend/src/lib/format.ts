import type { MaterialUnit } from "../api/types";

export function roundQty(value: string | number | null | undefined): string {
  return Math.round(Number(value ?? 0)).toString();
}

// Materials measured in "each" can't have fractional quantities — you can't have half
// a screw. Mirrors the backend's validate_qty_for_unit (app/services/validation.py).
export function wholeNumberStepFor(unit: MaterialUnit | null | undefined): string {
  return unit === "each" ? "1" : "any";
}

export function normalizeQtyForUnit(value: string, unit: MaterialUnit | null | undefined): string {
  if (unit !== "each" || value.trim() === "") return value;
  const n = Number(value);
  return Number.isNaN(n) ? value : Math.round(n).toString();
}

// A reorder_threshold of 0 means "don't track reordering for this material" — never
// flag it as low, no matter how little (or none) is on hand.
export function isLowStock(currentQty: string, reorderThreshold: string): boolean {
  const threshold = Number(reorderThreshold);
  return threshold > 0 && Number(currentQty) <= threshold;
}

// A compact date for dense rows (stock history, PO lists): "20 Aug". No year — this is for
// browsing recent movements, not for telling one January apart from the next.
export function formatDayMonth(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

// The quantity fields every sellable-bearing row carries. Both Product and Variant
// satisfy it, so the one derivation below serves the product header, the variant rows
// and the products list without any of them drifting from the others.
export interface SellableInput {
  current_stock: number;
  allocated_qty: number;
  max_buildable: number | null;
  expected_max_buildable: number | null;
  max_sellable: number | null;
  max_sellable_reason: string | null;
  expected_max_sellable: number | null;
  theoretical_max_sellable: number | null;
  theoretical_max_sellable_reason: string | null;
}

export interface SellableSummary {
  /** The figure actually pushed to marketplaces — the one number that matters. */
  headline: number | null;
  /** Already built and unreserved. */
  builtFree: number;
  /** Buildable from raw materials on hand, on top of builtFree. */
  buildable: number | null;
  /** Only set when something OTHER than stock/materials is holding the number down. */
  capLabel: string | null;
  /** Sellable once open purchase orders land — null unless that's actually more. */
  expected: number | null;
}

/**
 * Collapses the eight per-product quantity fields into the four a person actually reads.
 *
 * `platformCeilingQty` lives on the Product, so variant callers pass their parent's; it's
 * only needed for `expected`, which variant rows don't render.
 */
export function sellableSummary(
  item: SellableInput,
  { pushBuildableCapacity, platformCeilingQty = null }: { pushBuildableCapacity: boolean; platformCeilingQty?: number | null }
): SellableSummary {
  const builtFree = item.current_stock - item.allocated_qty;
  const headline = pushBuildableCapacity ? item.theoretical_max_sellable : item.max_sellable;
  const reason = pushBuildableCapacity ? item.theoretical_max_sellable_reason : item.max_sellable_reason;

  // "stock" and "materials" mean the headline already equals what stock and materials
  // allow — there's nothing to explain, and the old wording ("nothing built") claimed
  // zero even when units were built. Only a genuinely external limit gets a badge.
  const capLabel =
    reason === "packaging"
      ? "limited by packaging"
      : reason === "ceiling"
        ? `capped at ${headline}`
        : null;

  return { headline, builtFree, buildable: item.max_buildable, capLabel, expected: expectedSellable(item, builtFree, platformCeilingQty) };
}

function expectedSellable(item: SellableInput, builtFree: number, platformCeilingQty: number | null): number | null {
  // Only worth a line when open purchase orders genuinely raise the ceiling.
  if (item.max_buildable == null || item.expected_max_buildable == null) return null;
  if (item.expected_max_buildable <= item.max_buildable) return null;
  if (item.expected_max_sellable == null) return null;
  // expected_max_sellable is materials-and-packaging only — the backend never adds free
  // stock to it (combine_expected_max_sellable in kitting.py), which is why the raw
  // field can read LOWER than today's figure. Adding builtFree here is what makes the
  // two comparable. Slight overcount when packaging is the binding constraint, since
  // expected packaging capacity isn't exposed to the frontend to min() against.
  const raw = builtFree + item.expected_max_sellable;
  return platformCeilingQty == null ? raw : Math.min(raw, platformCeilingQty);
}
