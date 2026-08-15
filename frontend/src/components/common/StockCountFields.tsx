import type { ABCClass, ResolvedClassification } from "../../api/types";

const TIERS: ABCClass[] = ["A", "B", "C"];

/** Empty string is "inherit" — a <select> can't hold null, and inherit is a real choice here
 * (follow the category/type, then the shop default) rather than a missing one. */
const INHERIT = "";

function formatDate(iso: string | null): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleDateString();
}

/**
 * Why a value is what it is, in words.
 *
 * The point of showing provenance at all: "C" on its own gives no clue which of the three
 * levels to go and change. `class_source` comes from the server, which is the only place the
 * fallback order is implemented — deliberately not re-derived here.
 */
function describeSource(classification: ResolvedClassification, groupLabel: string | null): string {
  switch (classification.class_source) {
    case "item":
      return "set on this item";
    case "group":
      return groupLabel ? `from ${groupLabel}` : "from its group";
    default:
      return "the default";
  }
}

/**
 * The stock-counting controls shared by the material and product detail pages: the two
 * override inputs plus a read-only summary of what they currently resolve to.
 *
 * One component rather than two near-identical blocks, because the interesting part — saying
 * where an inherited value came from and when the item is next due — is display logic that
 * would otherwise drift between the two pages.
 */
export function StockCountFields({
  abcClass,
  intervalDays,
  classification,
  groupLabel,
  onAbcClassChange,
  onIntervalDaysChange,
}: {
  abcClass: ABCClass | null;
  /** Kept as a string because it's bound to a free-text number input; "" means no override. */
  intervalDays: string;
  classification: ResolvedClassification | null;
  /** What the middle level is called for this item — "the Packaging category", "the Coaster
   * type" — so an inherited value can name its source. Null when the item has no group. */
  groupLabel: string | null;
  onAbcClassChange: (next: ABCClass | null) => void;
  onIntervalDaysChange: (next: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Count tier
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={abcClass ?? INHERIT}
            onChange={(e) => onAbcClassChange(e.target.value === INHERIT ? null : (e.target.value as ABCClass))}
          >
            <option value={INHERIT}>Inherit</option>
            {TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Count every (days)
          <input
            type="number"
            min="1"
            step="1"
            placeholder={classification ? String(classification.interval_days) : ""}
            className="w-28 rounded border border-slate-300 px-2 py-1"
            value={intervalDays}
            onChange={(e) => onIntervalDaysChange(e.target.value)}
          />
        </label>
      </div>
      {classification && (
        <p className="text-xs text-slate-500">
          Tier <strong>{classification.abc_class}</strong> ({describeSource(classification, groupLabel)}), counted
          every {classification.interval_days} days. Last counted {formatDate(classification.last_stock_take_at)}
          {classification.last_stock_take_at && ` — next due ${formatDate(classification.next_due_at)}`}
          {classification.is_due && (
            <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
              {classification.days_overdue === null
                ? "Never counted"
                : classification.days_overdue === 0
                  ? "Due now"
                  : `${classification.days_overdue} days overdue`}
            </span>
          )}
        </p>
      )}
    </div>
  );
}
