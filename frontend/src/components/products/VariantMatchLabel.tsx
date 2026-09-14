import type { VariationMappingEntryBase } from "../../api/platforms";

// The left-hand side of one variation-mapping row: the StockSmith unit, its attribute
// values, and how confident the proposal was about the pre-filled pick. "count_only"
// is a positional guess worth a glance; "unmatched" means nothing was pre-filled.
export function VariantMatchLabel({
  entry,
}: {
  entry: VariationMappingEntryBase;
}) {
  const attrs = Object.entries(entry.stockssmith_attributes)
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
  return (
    <span>
      {entry.variant_name ?? "(product)"}
      {attrs && <span className="text-slate-500"> — {attrs}</span>}
      {entry.match_confidence !== "exact" && (
        <span className="ml-1 text-xs text-amber-600">
          ({entry.match_confidence === "count_only" ? "check this" : "pick one"})
        </span>
      )}
    </span>
  );
}
