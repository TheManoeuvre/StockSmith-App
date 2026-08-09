import type { BackupManifest } from "../../api/backups";

/**
 * Shared presentation helpers for backups.
 *
 * Deliberately here rather than in lib/format.ts: these are specific to how a backup is
 * described, and both the list and the restore panel have to word it identically — "restore the
 * one with 120 products" only works if both places count the same way.
 */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function formatWhen(iso: string | null | undefined, style: "medium" | "long" = "medium"): string {
  if (!iso) return "an unknown time";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: style, timeStyle: "short" });
}

/** "120 products, 88 materials" — non-zero counts only, so an empty backup says so plainly. */
export function describeContents(counts: BackupManifest["counts"]): string {
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([table, n]) => `${n} ${table.replace(/_/g, " ")}`);
  return parts.length > 0 ? parts.join(", ") : "no records";
}
