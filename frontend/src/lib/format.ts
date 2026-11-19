import type { MaterialUnit } from "../api/types";

// Whole units for a dense glance. Pass the unit where it is known: a unit bounded to decimal places
// (metres at two) keeps them, because rounding 1.5 m to "2" would be a lie about the stock.
export function roundQty(
  value: string | number | null | undefined,
  unit?: MaterialUnit | null,
): string {
  const places = unitRule(unit)?.decimal_places;
  if (places) return Number(value ?? 0).toFixed(places);
  return Math.round(Number(value ?? 0)).toString();
}

// A quantity for dense read rows: the whole number when it is one, the trimmed decimal
// when it genuinely has a fraction. The backend stores qty as Numeric(14,4), so a plain
// "30" comes back as "30.0000" — showing that in a list is just noise.
export function displayQty(value: string | number | null | undefined): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? String(n) : String(value ?? "");
}

// ---- Units of measure -------------------------------------------------------------------
//
// Units are rows in the database (Settings → Lists), each carrying how fine a quantity may be
// and whether its name is shown. These helpers are called from dozens of render paths that hold
// only a unit *name*, so the rules live in a module-level table that useMaterialUnits() fills
// from the API, rather than being threaded through every call site. Until it has loaded — and for
// a name that has no row — the table holds the three units the app shipped with, so a first
// paint reads the same as it always did.

export interface UnitRule {
  /** null = any precision; 0 = whole numbers; N = at most N places, displayed to N. */
  decimal_places: number | null;
  show_suffix: boolean;
}

const BUILT_IN_RULES: Record<string, UnitRule> = {
  g: { decimal_places: null, show_suffix: true },
  ml: { decimal_places: null, show_suffix: true },
  each: { decimal_places: 0, show_suffix: false },
};

let unitRules: Record<string, UnitRule> = { ...BUILT_IN_RULES };

/** Replace the rule table. Called by useMaterialUnits() whenever the units list loads or changes. */
export function setUnitRules(units: { name: string; decimal_places: number | null; show_suffix: boolean }[]): void {
  unitRules = Object.fromEntries(
    units.map((u) => [u.name, { decimal_places: u.decimal_places, show_suffix: u.show_suffix }]),
  );
}

export function unitRule(unit: MaterialUnit | null | undefined): UnitRule | undefined {
  return unit ? unitRules[unit] : undefined;
}

/** True for a unit that only takes whole numbers (what "each" always was). */
export function isWholeNumberUnit(unit: MaterialUnit | null | undefined): boolean {
  return unitRule(unit)?.decimal_places === 0;
}

/** The unit's name beside a number, or "" for a unit that doesn't show it. A unit with no rule
 *  shows its name: better a stray suffix than a quantity that has silently lost its unit. */
export function unitLabel(unit: MaterialUnit | null | undefined): string {
  if (!unit) return "";
  return unitRule(unit)?.show_suffix === false ? "" : unit;
}

// The unit, as a display suffix with its leading space. "each" carries none — spelling it out on
// every row of a coaster order reads as clutter, not information.
export function unitSuffix(unit: MaterialUnit | null | undefined): string {
  const label = unitLabel(unit);
  return label ? ` ${label}` : "";
}

// A quantity in its unit's own precision: a bounded unit is shown to exactly that many places
// ("1.50" for metres at two), an unbounded one trimmed as displayQty does.
export function displayQtyForUnit(
  value: string | number | null | undefined,
  unit: MaterialUnit | null | undefined,
): string {
  const places = unitRule(unit)?.decimal_places;
  const n = Number(value ?? 0);
  if (places == null || !Number.isFinite(n)) return displayQty(value);
  return n.toFixed(places);
}

// displayQtyForUnit + unitSuffix: "30", "100 g", "12.5 ml", "1.50 m".
export function qtyWithUnit(
  value: string | number | null | undefined,
  unit: MaterialUnit | null | undefined,
): string {
  return `${displayQtyForUnit(value, unit)}${unitSuffix(unit)}`;
}

// The `step` for a quantity input. A unit bounded to N places steps by one in the last place; an
// unbounded one takes anything. Mirrors the backend's validate_qty_for_unit
// (app/services/validation.py), which is what actually refuses a value that is too fine.
export function wholeNumberStepFor(unit: MaterialUnit | null | undefined): string {
  const places = unitRule(unit)?.decimal_places;
  if (places == null) return "any";
  return places === 0 ? "1" : (10 ** -places).toFixed(places);
}

export function normalizeQtyForUnit(value: string, unit: MaterialUnit | null | undefined): string {
  const places = unitRule(unit)?.decimal_places;
  if (places == null || value.trim() === "") return value;
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  return places === 0 ? Math.round(n).toString() : n.toFixed(places);
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

// A due-date span for a row that stands for several orders: one date when they all fall on one
// day, otherwise a range — "15 – 18 Aug" on a UK computer, "Aug 15 – 18" on a US one. Formatted by
// the computer's own locale (Intl's formatRange knows how each language shortens a range; the
// hand-built "15–Aug 18" it replaced read wrongly outside the UK). Null/missing dates are skipped;
// "-" when none of them has one. `locale` is for tests.
export function formatDayMonthRange(isos: (string | null | undefined)[], locale?: string): string {
  const times = isos
    .filter((v): v is string => !!v)
    .map((v) => new Date(v))
    .sort((a, b) => a.getTime() - b.getTime());
  if (times.length === 0) return "-";
  const format = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "short" });
  const first = times[0];
  const last = times[times.length - 1];
  if (format.format(first) === format.format(last)) return format.format(first);
  // formatRange is ES2021; this project's TypeScript lib is ES2020, though every webview StockSmith
  // runs in has had it for years.
  return (format as Intl.DateTimeFormat & { formatRange(a: Date, b: Date): string }).formatRange(first, last);
}

// The quantity fields every sellable-bearing row carries. Both Product and Variant
// satisfy it, so the one derivation below serves the product header, the variant rows
// and the products list without any of them drifting from the others.
export interface SellableInput {
  current_stock: number;
  allocated_qty: number;
  max_buildable: number | null;
  expected_max_buildable: number | null;
  max_buildable_incl_fallbacks: number | null;
  expected_max_buildable_incl_fallbacks: number | null;
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
  /** Buildable from the BOM's own raw materials on hand, on top of builtFree. */
  buildable: number | null;
  /**
   * Buildable once each material's fallback substitutes are counted too — the figure the
   * sellable numbers actually build on. Only set when it beats `buildable`, so callers
   * can render it as a qualifier ("10 buildable (20 incl. fallbacks)") and omit it when
   * fallbacks add nothing.
   */
  buildableInclFallbacks: number | null;
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

  const buildableInclFallbacks =
    item.max_buildable != null &&
    item.max_buildable_incl_fallbacks != null &&
    item.max_buildable_incl_fallbacks > item.max_buildable
      ? item.max_buildable_incl_fallbacks
      : null;

  return {
    headline,
    builtFree,
    buildable: item.max_buildable,
    buildableInclFallbacks,
    capLabel,
    expected: expectedSellable(item, builtFree, platformCeilingQty),
  };
}

/** "20 incl. fallbacks" when fallbacks lift the buildable figure, else null. */
export function inclFallbacksNote(s: SellableSummary): string | null {
  return s.buildableInclFallbacks == null ? null : `${s.buildableInclFallbacks} incl. fallbacks`;
}

function expectedSellable(item: SellableInput, builtFree: number, platformCeilingQty: number | null): number | null {
  // Only worth a line when open purchase orders genuinely raise the ceiling. Compared on
  // the fallback-pooled pair, since that's what expected_max_sellable is built from.
  if (item.max_buildable_incl_fallbacks == null || item.expected_max_buildable_incl_fallbacks == null) return null;
  if (item.expected_max_buildable_incl_fallbacks <= item.max_buildable_incl_fallbacks) return null;
  if (item.expected_max_sellable == null) return null;
  // expected_max_sellable is materials-and-packaging only — the backend never adds free
  // stock to it (combine_expected_max_sellable in kitting.py), which is why the raw
  // field can read LOWER than today's figure. Adding builtFree here is what makes the
  // two comparable. Slight overcount when packaging is the binding constraint, since
  // expected packaging capacity isn't exposed to the frontend to min() against.
  const raw = builtFree + item.expected_max_sellable;
  return platformCeilingQty == null ? raw : Math.min(raw, platformCeilingQty);
}
