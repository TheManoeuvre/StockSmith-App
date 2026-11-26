// The currency every cost, margin and total is shown in: Settings > Default currency.
//
// Held in a module-level variable (pushed in by hooks/useDisplayCurrency.ts, the same way
// lib/format.ts holds unit rules) because these are plain functions called from dozens of
// render paths that hold only a number. No conversion happens anywhere: changing the setting
// relabels existing figures. Orders keep their own currency and pass it to formatMoney.
let displayCurrency = "GBP";

export function setDisplayCurrency(code: string): void {
  displayCurrency = code;
}

export function getDisplayCurrency(): string {
  return displayCurrency;
}

/**
 * The short symbol for a currency: "£", "$", "€". Falls back to the code plus a space
 * ("CHF ") for anything without one, so an amount never reads as a bare number glued to
 * letters. Locale-neutral on purpose: the symbol is the currency's, not the computer's.
 */
export function currencySymbol(code: string = displayCurrency): string {
  try {
    const symbol = new Intl.NumberFormat("en", { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
      .formatToParts(0)
      .find((part) => part.type === "currency")?.value;
    if (symbol) return /^[A-Za-z]+$/.test(symbol) ? `${symbol} ` : symbol;
  } catch {
    // An unrecognised code: fall through to showing it as written.
  }
  return `${code} `;
}

/** An amount in the display currency, e.g. "£12.34". Null/undefined read as "-". */
export function formatAmount(value: number | string | null | undefined, decimals = 2): string {
  if (value == null || value === "") return "-";
  return `${currencySymbol()}${Number(value).toFixed(decimals)}`;
}

/** An order-level amount, in that order's own currency (the display currency if it has none). */
export function formatMoney(value: string | null, currency: string | null): string {
  if (value == null) return "-";
  return `${currencySymbol(currency ?? displayCurrency)}${Number(value).toFixed(2)}`;
}

// Ceiling (round up), not round-to-nearest — a fraction of a penny above £0.00 still
// displays as 0.01. The true unrounded value keeps driving every calculation; this is
// display-only, for average/unit cost figures specifically (not aggregates/totals).
export function ceilToPenny(value: number): number {
  return Math.ceil(value * 100) / 100;
}

export function formatUnitCost(value: string | number | null | undefined): string {
  if (value == null) return "-";
  return `${currencySymbol()}${ceilToPenny(Number(value)).toFixed(2)}`;
}
