/**
 * Shared formatting for the weeks-of-supply / time-to-stockout forecast that the backend
 * (`services/forecasting.py`) now returns on the dashboard low-stock panel, the materials
 * list, and the material detail panel.
 */

import type { StockoutStatus } from "../api/types";

export type { StockoutStatus };

/** Badge (bg + text) classes, keyed by status. `ok` is intentionally muted — a healthy
 *  material doesn't need a loud pill. */
export const STOCKOUT_BADGE_CLASS: Record<StockoutStatus, string> = {
  critical: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  insufficient_data: "bg-slate-100 text-slate-600",
  ok: "bg-emerald-100 text-emerald-800",
};

export const STOCKOUT_LABEL: Record<StockoutStatus, string> = {
  critical: "Critical",
  warning: "Warning",
  insufficient_data: "Not enough history",
  ok: "Healthy",
};

/** Text colour for a bare figure (e.g. a table cell), keyed by status. */
export const STOCKOUT_TEXT_CLASS: Record<StockoutStatus, string> = {
  critical: "text-red-600",
  warning: "text-amber-700",
  insufficient_data: "text-slate-400",
  ok: "",
};

interface WeeksInput {
  weeks_of_supply: string | null;
  fg_buffer_weeks?: string | null;
}

/** Long form, for the dashboard and the detail panel: "3.2 wks", or with the finished-goods
 *  contribution spelled out when it's material. `—` when there's no forecast. */
export function formatWeeksOfSupply(m: WeeksInput): string {
  if (m.weeks_of_supply == null) return "—";
  const weeks = Number(m.weeks_of_supply);
  const fgWeeks = m.fg_buffer_weeks != null ? Number(m.fg_buffer_weeks) : 0;
  if (fgWeeks > 0.05) {
    return `${weeks.toFixed(1)} wks (incl. ${fgWeeks.toFixed(1)} wk from finished-goods stock)`;
  }
  return `${weeks.toFixed(1)} wks`;
}

/** Compact form for the list column: "3.2 wk" or "—". */
export function formatWeeksShort(weeksOfSupply: string | null): string {
  if (weeksOfSupply == null) return "—";
  return `${Number(weeksOfSupply).toFixed(1)} wk`;
}
