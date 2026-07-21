// Masks a buyer's full name down to first initial + surname for display in broad listings
// (e.g. the orders list) — the order detail page (a single, deliberate lookup) still shows
// the full name as-is.
export function maskBuyerName(name: string | null): string | null {
  if (!name || !name.trim()) return null;
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0];
  return `${parts[0][0]} ${parts[parts.length - 1]}`;
}
