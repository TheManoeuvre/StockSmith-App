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

// Short tag for a Ship-row number (max_sellable/expected_max_sellable) explaining why
// it's lower than its Build-row counterpart (max_buildable/expected_max_buildable) — or
// null if it isn't actually lower, or there's nothing to blame. "materials" never
// produces a tag: it means the Ship value already equals the Build value exactly.
export function sellableReasonTag(
  value: number | null,
  buildValue: number | null,
  reason: string | null
): string | null {
  if (value == null || buildValue == null || value >= buildValue) return null;
  if (reason === "stock") return "(nothing built)";
  if (reason === "packaging") return "(packaging)";
  if (reason === "ceiling") return "(capped)";
  return null;
}
