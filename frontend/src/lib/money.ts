export function formatMoney(value: string | null, currency: string | null): string {
  if (value == null) return "—";
  const symbol = currency === "GBP" ? "£" : currency === "EUR" ? "€" : currency === "USD" || !currency ? "$" : `${currency} `;
  return `${symbol}${Number(value).toFixed(2)}`;
}

// Ceiling (round up), not round-to-nearest — a fraction of a penny above £0.00 still
// displays as £0.01. The true unrounded value keeps driving every calculation; this is
// display-only, for average/unit cost figures specifically (not aggregates/totals).
export function ceilToPenny(value: number): number {
  return Math.ceil(value * 100) / 100;
}

export function formatUnitCost(value: string | number | null | undefined): string {
  if (value == null) return "—";
  return `£${ceilToPenny(Number(value)).toFixed(2)}`;
}
