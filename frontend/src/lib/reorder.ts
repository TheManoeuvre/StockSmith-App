/**
 * The forecasting maths behind the new-purchase panel: how many weeks of cover a delivery
 * buys, and how much of a low material to reorder. Pure functions — `lib/forecast.ts` is
 * shared *formatting* for the figures the backend already returns; this is computation the
 * panel does on top of them, mirroring the reviewed design's `coverAt` / `suggestQty`.
 */

import type { Material, MaterialUnit } from "../api/types";

const DAY_MS = 86_400_000;

/** Whole days from `from` to the ISO date `to`. Negative when `to` is already past. */
function daysUntil(from: Date, to: string): number {
  return Math.round((new Date(to).getTime() - from.getTime()) / DAY_MS);
}

type CoverInput = Pick<
  Material,
  "current_qty" | "consumption_rate_per_week" | "on_order_qty"
>;

/**
 * Weeks of cover a material has the moment `arrivalDate`'s delivery lands — stock run down
 * at the consumption rate over the lead time, then everything inbound (already on order
 * plus this PO's `extraInboundQty`) added back.
 *
 * `null` when there's no consumption rate to divide by (thin sales history, or a mutation
 * response that omits the forecast fields). Callers must render "—", never "NaN wk".
 */
export function coverOnArrival(
  m: CoverInput,
  arrivalDate: string | null,
  extraInboundQty: number,
  leadTimeDays: number | null,
  now: Date = new Date(),
): number | null {
  const ratePerWeek = Number(m.consumption_rate_per_week);
  if (!Number.isFinite(ratePerWeek) || ratePerWeek <= 0) return null;

  const total =
    projectedOnHand(m, arrivalDate, leadTimeDays, now) +
    (Number(m.on_order_qty ?? 0) || 0) +
    (Number(extraInboundQty) || 0);
  return total / ratePerWeek;
}

/**
 * On-hand a material will have when `arrivalDate`'s delivery lands, before this PO's line
 * is counted — the figure behind "N on hand by <date>" on a new-purchase line. Falls back
 * to a lead-time projection when no `arrivalDate` is set, and to plain on-hand when there
 * is no consumption rate.
 */
export function projectedOnHand(
  m: Pick<Material, "current_qty" | "consumption_rate_per_week">,
  arrivalDate: string | null,
  leadTimeDays: number | null,
  now: Date = new Date(),
): number {
  const onHandNow = Math.max(0, Number(m.current_qty) || 0);
  const ratePerWeek = Number(m.consumption_rate_per_week);
  if (!Number.isFinite(ratePerWeek) || ratePerWeek <= 0) return onHandNow;

  const leadDays = arrivalDate
    ? Math.max(0, daysUntil(now, arrivalDate))
    : Math.max(0, leadTimeDays ?? 0);
  return Math.max(0, onHandNow - (ratePerWeek / 7) * leadDays);
}

/** Quantity granularity a reorder suggestion rounds up to: 100 for bulk (g/ml), 1 for
 *  countable ("each"). */
export function reorderGranularity(unit: MaterialUnit): number {
  return unit === "each" ? 1 : 100;
}

/**
 * A suggested reorder quantity for a material running low. Its own `typical_reorder_qty`
 * wins when set; otherwise enough to clear the reorder threshold or ~8 weeks of
 * consumption (whichever is larger), rounded up to `reorderGranularity`.
 */
export function suggestReorderQty(m: Material): number {
  const typical = Number(m.typical_reorder_qty);
  if (Number.isFinite(typical) && typical > 0) return typical;

  const onHand = Number(m.current_qty) || 0;
  const threshold = Number(m.reorder_threshold) || 0;
  const ratePerWeek = Number(m.consumption_rate_per_week) || 0;
  const target = Math.max(threshold - onHand, ratePerWeek * 8 - onHand, 0);

  const grain = reorderGranularity(m.unit);
  return Math.max(grain, Math.ceil(target / grain) * grain);
}

/**
 * How much of `m` to put on a draft purchase raised straight from a "Time to stockout"
 * alert: the lowest whole multiple of its own `typical_reorder_qty` that lifts projected
 * cover back over the forecast warning threshold.
 *
 * `warningWeeks` is GeneralSettings.forecast_warning_weeks; the material's lead time is
 * added on top, mirroring how services/forecasting.py decides "warning" in the first place,
 * so the ordered quantity is actually enough to clear the alert rather than landing just
 * inside it. Each unit buys `1 / rate` weeks of cover, so the shortfall in weeks times the
 * rate is the units needed before rounding up to a whole number of typical-order steps.
 *
 * Falls back to `suggestReorderQty` when there's no typical reorder qty to step by, and to
 * a plain reorder-threshold top-up when there's no consumption rate to project weeks from.
 */
export function qtyToClearWarning(m: Material, warningWeeks: number): number {
  const typical = Number(m.typical_reorder_qty);
  if (!Number.isFinite(typical) || typical <= 0) return suggestReorderQty(m);

  const rate = Number(m.consumption_rate_per_week) || 0;
  const currentWeeks =
    m.weeks_of_supply != null ? Number(m.weeks_of_supply) : null;
  // 5 business days per week — the same conversion services/forecasting.py uses to fold a
  // lead time in business days into its weeks-based thresholds.
  const leadWeeks = Math.max(0, Number(m.lead_time_days) || 0) / 5;
  const targetWeeks = Math.max(0, warningWeeks) + leadWeeks;

  let needed: number;
  if (rate > 0 && currentWeeks != null && Number.isFinite(currentWeeks)) {
    needed = (targetWeeks - currentWeeks) * rate;
  } else {
    // No usable forecast — top the on-hand/on-order position up over the manual floor.
    const position =
      (Number(m.current_qty) || 0) -
      (Number(m.allocated_qty) || 0) +
      (Number(m.on_order_qty) || 0);
    needed = (Number(m.reorder_threshold) || 0) - position;
  }

  const multiples = Math.max(1, Math.ceil(needed / typical));
  return multiples * typical;
}

export type CoverTone = "red" | "amber" | "green" | "muted";

/** Red under 2 weeks of cover, amber under 4, green above; muted with no figure. Matches
 *  the reviewed design's per-line cover colouring. */
export function coverTone(weeks: number | null): CoverTone {
  if (weeks == null || !Number.isFinite(weeks)) return "muted";
  if (weeks < 2) return "red";
  if (weeks < 4) return "amber";
  return "green";
}
