import type { InsightsChannel, InsightsGranularity } from "../api/insights";

// --- Dates ------------------------------------------------------------------------------
// ISO calendar days ("2026-10-25") throughout, handled in UTC so a day is never lost to a
// daylight-saving change. The backend buckets on UTC days too.

const DAY_MS = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function toIso(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function parse(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function format(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  return format(parse(iso) + days * DAY_MS);
}

export function daysBetween(startIso: string, endIso: string): number {
  return Math.round((parse(endIso) - parse(startIso)) / DAY_MS);
}

/** "25 Oct", or "25 Oct 2026" when `withYear`. */
export function formatDate(iso: string, withYear = false): string {
  const d = new Date(parse(iso));
  const base = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  return withYear ? `${base} ${d.getUTCFullYear()}` : base;
}

// --- Ranges -----------------------------------------------------------------------------

export type RangePreset = "7d" | "30d" | "90d" | "year" | "12m";

export const RANGE_PRESETS: { value: RangePreset; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "year", label: "This year" },
  { value: "12m", label: "12 months" },
];

export interface ResolvedRange {
  start: string;
  end: string;
  /** The period compared against: equal length and immediately before, except for "This
   *  year", which is the same dates last year so a part-year isn't set against a whole one. */
  compare: { start: string; end: string };
  /** "This year" only: the whole of the calendar year, so the chart can show January to
   *  December with the months still to come left empty. */
  fullYear: { start: string; end: string } | null;
  /** The whole of last year, for the comparison line beyond today. "This year" only. */
  previousFullYear: { start: string; end: string } | null;
  label: string;
}

/** The same calendar day in the previous year. 29 Feb falls back to 28 Feb. */
function sameDayLastYear(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const lastDay = new Date(Date.UTC(y - 1, m, 0)).getUTCDate();
  return `${y - 1}-${String(m).padStart(2, "0")}-${String(Math.min(d, lastDay)).padStart(2, "0")}`;
}

export function resolveRange(preset: RangePreset, today: Date): ResolvedRange {
  const end = toIso(today);
  const year = Number(end.slice(0, 4));

  if (preset === "year") {
    const start = `${year}-01-01`;
    return {
      start,
      end,
      compare: { start: `${year - 1}-01-01`, end: sameDayLastYear(end) },
      fullYear: { start, end: `${year}-12-31` },
      previousFullYear: { start: `${year - 1}-01-01`, end: `${year - 1}-12-31` },
      label: `1 Jan – 31 Dec ${year} (to ${formatDate(end)} so far)`,
    };
  }

  const length = preset === "7d" ? 7 : preset === "30d" ? 30 : preset === "90d" ? 90 : 365;
  const start = addDays(end, -(length - 1));
  const compareEnd = addDays(start, -1);
  return {
    start,
    end,
    compare: { start: addDays(compareEnd, -(length - 1)), end: compareEnd },
    fullYear: null,
    previousFullYear: null,
    label: `${formatDate(start, true)} – ${formatDate(end, true)}`,
  };
}

// --- Buckets and chart slots ------------------------------------------------------------

/** First day of the bucket that contains `iso`. Weeks start on Monday, as on the backend. */
export function bucketStart(iso: string, granularity: InsightsGranularity): string {
  if (granularity === "day") return iso;
  if (granularity === "month") return `${iso.slice(0, 7)}-01`;
  const weekday = (new Date(parse(iso)).getUTCDay() + 6) % 7; // Monday = 0
  return addDays(iso, -weekday);
}

export function nextBucket(iso: string, granularity: InsightsGranularity): string {
  if (granularity === "day") return addDays(iso, 1);
  if (granularity === "week") return addDays(iso, 7);
  const [y, m] = iso.split("-").map(Number);
  return m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
}

/** Every bucket start from `start` to `end`: the x positions of a chart's points. */
export function bucketSlots(start: string, end: string, granularity: InsightsGranularity): string[] {
  const slots: string[] = [];
  let cursor = bucketStart(start, granularity);
  while (cursor <= end) {
    slots.push(cursor);
    cursor = nextBucket(cursor, granularity);
  }
  return slots;
}

/** Which slots get an axis label, and what it says. Months label the first slot of each month;
 *  shorter ranges get about five evenly spaced dates. */
export function axisLabels(
  slots: string[],
  granularity: InsightsGranularity,
  monthly: boolean,
): { index: number; label: string }[] {
  if (slots.length === 0) return [];
  if (monthly) {
    // A week belongs to the month most of its days fall in (its Thursday), so the week that
    // starts on 29 Dec and holds 1–4 Jan is January's first, not a stray December.
    const month = (slot: string) => (granularity === "week" ? addDays(slot, 3) : slot).slice(0, 7);
    const labels: { index: number; label: string }[] = [];
    slots.forEach((slot, index) => {
      if (index === 0 || month(slot) !== month(slots[index - 1])) {
        labels.push({ index, label: MONTHS[Number(month(slot).slice(5, 7)) - 1] });
      }
    });
    return labels;
  }
  const target = Math.min(5, slots.length);
  const picks = new Set<number>();
  for (let i = 0; i < target; i++) picks.add(Math.round((i * (slots.length - 1)) / Math.max(1, target - 1)));
  return [...picks].map((index) => ({
    index,
    label: granularity === "month" ? `${MONTHS[Number(slots[index].slice(5, 7)) - 1]} ${slots[index].slice(0, 4)}` : formatDate(slots[index]),
  }));
}

// --- Numbers ----------------------------------------------------------------------------

export function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** £1,234.50, or −£12.00. A null is "-": an unknown figure is never shown as £0. */
export function gbp(value: string | number | null | undefined, decimals = 2): string {
  const n = num(value);
  if (n === null) return "-";
  const text = Math.abs(n).toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return `${n < 0 ? "−" : ""}£${text}`;
}

/** Whole pounds for chart axes and tight labels: £12k above a thousand. */
export function gbpCompact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs >= 1000) return `${sign}£${(abs / 1000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  return `${sign}£${Math.round(abs)}`;
}

export function pct(value: string | number | null | undefined): string {
  const n = num(value);
  return n === null ? "-" : `${n.toFixed(1)}%`;
}

export function count(value: number): string {
  return Math.round(value).toLocaleString("en-GB");
}

export type DeltaTone = "good" | "bad" | "flat";

export interface Delta {
  text: string;
  tone: DeltaTone;
}

/** Change against the comparison period: direction and sign in the text, never colour alone.
 *  `lowerIsBetter` flips the tone for things like the refund rate. */
export function percentChange(current: number | null, previous: number | null, lowerIsBetter = false): Delta | null {
  if (current === null || previous === null || previous === 0) return null;
  const change = ((current - previous) / Math.abs(previous)) * 100;
  return deltaOf(change, "%", lowerIsBetter);
}

/** For figures that are themselves percentages, the change is in points. */
export function pointChange(current: number | null, previous: number | null, lowerIsBetter = false): Delta | null {
  if (current === null || previous === null) return null;
  return deltaOf(current - previous, " pts", lowerIsBetter);
}

function deltaOf(change: number, unit: string, lowerIsBetter: boolean): Delta {
  const rounded = Math.round(Math.abs(change) * 10) / 10;
  if (rounded === 0) return { text: `No change`, tone: "flat" };
  const up = change > 0;
  return {
    text: `${up ? "▲" : "▼"} ${rounded.toFixed(1)}${unit}`,
    tone: up !== lowerIsBetter ? "good" : "bad",
  };
}

/** A tidy axis top and four evenly spaced ticks for a chart whose largest value is `max`. */
export function niceScale(min: number, max: number, ticks = 4): { low: number; high: number; values: number[] } {
  const low = Math.min(0, min);
  const span = Math.max(max - low, 1e-9);
  const rough = span / ticks;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? 10 * magnitude;
  const lowTick = Math.floor(low / step) * step;
  const highTick = Math.ceil(max / step) * step || step;
  const values: number[] = [];
  for (let v = lowTick; v <= highTick + step / 1000; v += step) values.push(Math.round(v * 1e6) / 1e6);
  return { low: lowTick, high: highTick, values };
}

// --- Labels -----------------------------------------------------------------------------

export const CHANNEL_ORDER: InsightsChannel[] = ["etsy", "ebay", "square", "manual", "shopify"];

export const CHANNEL_LABELS: Record<InsightsChannel, string> = {
  etsy: "Etsy",
  ebay: "eBay",
  shopify: "Shopify",
  square: "Square",
  manual: "Manual",
};

export const ISSUE_LABELS: Record<string, string> = {
  cogs_pending: "COGS pending",
  postage_missing: "Postage missing",
  financials_not_synced: "Financials not synced",
  unmapped_lines: "Unmapped SKU",
};

// --- CSV --------------------------------------------------------------------------------

function csvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(header: string[], rows: (string | number | null | undefined)[][]): string {
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

/** Saves text as a file through a throwaway link, the same way api/client.downloadCsv does. */
export function saveCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
