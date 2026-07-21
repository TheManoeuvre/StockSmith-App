import type { ListingSyncStatus, ProductSyncStatus } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../../lib/platforms";

const UNIT_LABELS: Record<ListingSyncStatus, (label: string) => string> = {
  synced: (label) => `Synced with ${label}`,
  listing_not_active: (label) => `${label} listing not active`,
  not_found: (label) => `Not Found in ${label}`,
  not_tested: () => "Not yet tested",
};

const PRODUCT_LABELS: Record<ProductSyncStatus, (label: string) => string> = {
  synced: (label) => `Synced with ${label}`,
  partial: (label) => `Partial ${label} Sync`,
  not_found: (label) => `Not Found in ${label}`,
  not_tested: () => "Not yet tested",
};

export function PlatformSyncBadge({
  platform,
  status,
}: {
  platform: ListingPlatform;
  status: ListingSyncStatus | ProductSyncStatus;
}) {
  const label = PLATFORM_LABELS[platform];
  const text =
    (UNIT_LABELS as Record<string, (label: string) => string>)[status]?.(label) ??
    (PRODUCT_LABELS as Record<string, (label: string) => string>)[status]?.(label) ??
    status;
  // "synced" is the one positive/confirmed state — solid platform colour. Everything
  // else (partial/not-active/not-found/not-tested) uses the same hue at lower
  // saturation, so a platform stays visually identifiable by colour at a glance while
  // status is still conveyed by weight + the label text itself.
  const colorClass = status === "synced" ? PLATFORM_COLORS[platform].solid : PLATFORM_COLORS[platform].muted;
  return <span className={`rounded px-2 py-0.5 text-xs ${colorClass}`}>{text}</span>;
}
