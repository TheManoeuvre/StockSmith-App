export function formatMoney(value: string | null, currency: string | null): string {
  if (value == null) return "—";
  const symbol = currency === "GBP" ? "£" : currency === "EUR" ? "€" : currency === "USD" || !currency ? "$" : `${currency} `;
  return `${symbol}${Number(value).toFixed(2)}`;
}
