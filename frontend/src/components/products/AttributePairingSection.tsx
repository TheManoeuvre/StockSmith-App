import type { AttributeMap, AttributePair } from "../../api/platforms";

// Stage one of the variation-mapping proposal, made editable: which platform
// attribute ("Color", "Primary colour") is each StockSmith attribute ("Colour")?
// The backend pairs these automatically and flags how sure it was; the user can
// re-pair any of them, which re-runs the value matching underneath. Shared by both
// listing pickers since this step is identical on eBay and Etsy — only what the
// resulting rows are keyed by differs.
export function AttributePairingSection({
  pairs,
  platformNames,
  overrides,
  onChange,
  platformLabel,
  disabled,
}: {
  pairs: AttributePair[];
  platformNames: string[];
  overrides: AttributeMap;
  onChange: (stocksmithName: string, platformName: string | null) => void;
  platformLabel: "eBay" | "Etsy";
  disabled?: boolean;
}) {
  if (pairs.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
      <p className="text-sm font-medium">
        Match StockSmith attributes to {platformLabel} variation attributes
      </p>
      {pairs.map((pair) => {
        const value =
          pair.stocksmith_name in overrides
            ? overrides[pair.stocksmith_name]
            : pair.platform_name;
        return (
          <label
            key={pair.stocksmith_name}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span>
              {pair.stocksmith_name}
              {pair.source === "inferred" && (
                <span className="ml-1 text-xs text-amber-600">
                  (matched by values, check this)
                </span>
              )}
              {pair.source === "unmatched" && (
                <span className="ml-1 text-xs text-amber-600">(pick one)</span>
              )}
            </span>
            <select
              disabled={disabled}
              aria-label={`${platformLabel} attribute for ${pair.stocksmith_name}`}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs"
              value={value ?? ""}
              onChange={(e) =>
                onChange(pair.stocksmith_name, e.target.value || null)
              }
            >
              <option value="">— not on listing —</option>
              {platformNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </div>
  );
}
