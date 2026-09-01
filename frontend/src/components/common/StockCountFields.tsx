import type { ABCClass, ResolvedClassification } from "../../api/types";
import { formatDayMonth } from "../../lib/format";
import { FieldRow } from "./FieldRow";

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

/** Same idea as describeSource, but for interval_source — the count-cadence has its own,
 * separate fallback chain (item override -> tier default -> shop default). */
function describeIntervalSource(classification: ResolvedClassification): string {
  switch (classification.interval_source) {
    case "item":
      return "set on this item";
    case "tier":
      return `from tier ${classification.abc_class}`;
    default:
      return "the shop default";
  }
}

const OPEN_TAKE_LINE_LABELS: Record<string, string> = {
  pending: "not counted yet",
  counted: "counted, awaiting review",
  applied: "counted, applied",
  conflict: "needs review",
  accepted_system: "count accepted as system figure",
  skipped: "skipped",
};

/**
 * The stock-counting controls shared by the material and product detail pages: the two
 * override inputs plus a read-only summary of what they currently resolve to.
 *
 * One component rather than two near-identical blocks, because the interesting part — saying
 * where an inherited value came from and when the item is next due — is display logic that
 * would otherwise drift between the two pages.
 *
 * `layout="rows"` (materials' Counting tab) renders the same data as labelled stacked rows,
 * matching the reviewed design, plus an "On stock take" row the compact layout has no room
 * for. `layout="inline"` (the default — the product Details form) is the original compact
 * two-input-plus-prose block, unchanged.
 */
export function StockCountFields({
  abcClass,
  intervalDays,
  classification,
  groupLabel,
  onAbcClassChange,
  onIntervalDaysChange,
  layout = "inline",
  openTake = null,
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
  layout?: "inline" | "rows";
  /** The item's line on the currently-open stock take, if any. Rows layout only. */
  openTake?: { id: number; status: string } | null;
}) {
  const tierSelect = (
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
  );

  const intervalInput = (
    <input
      type="number"
      min="1"
      step="1"
      placeholder={classification ? String(classification.interval_days) : ""}
      className="w-28 rounded border border-slate-300 px-2 py-1"
      value={intervalDays}
      onChange={(e) => onIntervalDaysChange(e.target.value)}
    />
  );

  if (layout === "rows") {
    return (
      <div className="flex flex-col gap-3">
        <FieldRow label="On stock take">
          <p className={`text-sm ${openTake?.status === "conflict" ? "text-amber-700" : "text-slate-600"}`}>
            {openTake
              ? `Take #${openTake.id} · ${OPEN_TAKE_LINE_LABELS[openTake.status] ?? openTake.status}`
              : "Not on an open take"}
          </p>
        </FieldRow>
        <FieldRow label="ABC class">{tierSelect}</FieldRow>
        <FieldRow label="Count interval override">
          <div className="flex items-center gap-2">
            {intervalInput}
            <span className="text-xs text-slate-400">
              {intervalDays.trim() === "" ? "0 = inherit" : "days"}
            </span>
          </div>
        </FieldRow>
        <FieldRow label="Effective cadence">
          <p className="text-sm text-slate-600">
            {classification
              ? `Every ${classification.interval_days} days · ${describeIntervalSource(classification)}`
              : "—"}
          </p>
        </FieldRow>
        <FieldRow label="Last counted">
          <p className="text-sm text-slate-600">
            {classification?.last_stock_take_at ? formatDayMonth(classification.last_stock_take_at) : "Never"}
          </p>
        </FieldRow>
        <FieldRow label="Next due">
          {classification == null ? (
            <p className="text-sm text-slate-600">—</p>
          ) : classification.days_overdue === null ? (
            <p className="text-sm text-amber-700">Never counted</p>
          ) : classification.days_overdue > 0 ? (
            <p className="text-sm text-amber-700">{classification.days_overdue} days overdue</p>
          ) : classification.is_due ? (
            <p className="text-sm text-amber-700">Due today</p>
          ) : (
            <p className="text-sm text-slate-600">
              {classification.next_due_at ? `On schedule · due ${formatDayMonth(classification.next_due_at)}` : "On schedule"}
            </p>
          )}
        </FieldRow>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Count tier
          {tierSelect}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Count every (days)
          {intervalInput}
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
