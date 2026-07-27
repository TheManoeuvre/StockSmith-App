export function roundQty(value: string | number | null | undefined): string {
  return Math.round(Number(value ?? 0)).toString();
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
