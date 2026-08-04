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
  compact = false,
}: {
  platform: ListingPlatform;
  status: ListingSyncStatus | ProductSyncStatus;
  // Renders just the platform name instead of the full sentence, for places that show
  // several badges side by side (the products list's Stores column). The sentence isn't
  // lost — it moves to the tooltip, so hovering still explains the state.
  compact?: boolean;
}) {
  const label = PLATFORM_LABELS[platform];
  const sentence =
    (UNIT_LABELS as Record<string, (label: string) => string>)[status]?.(label) ??
    (PRODUCT_LABELS as Record<string, (label: string) => string>)[status]?.(label) ??
    status;
  const text = compact ? label : sentence;
  // "synced" is the one positive/confirmed state — solid platform colour. Everything
  // else (partial/not-active/not-found/not-tested) uses the same hue at lower
  // saturation, so a platform stays visually identifiable by colour at a glance while
  // status is still conveyed by weight + the label text itself.
  const colorClass = status === "synced" ? PLATFORM_COLORS[platform].solid : PLATFORM_COLORS[platform].muted;
  // eBay-specific: "not found" here almost always means the listing was created via
  // eBay's Seller Hub UI and was never migrated to an Inventory API object, not that
  // the SKU is genuinely missing — confirmed live via a direct GET on a known-live SKU
  // returning 404 on both getInventoryItem and getOffers. eBay's Inventory API (which
  // this app's sync is built on) simply has no record of an un-migrated listing at all.
  const ebayNotFoundHint =
    platform === "ebay" && (status === "not_found" || status === "partial")
      ? "eBay reports this SKU as not found via its Inventory API. This usually means the listing was created through eBay's Seller Hub UI and hasn't been migrated to an Inventory API object yet — use \"Find unmigrated listing\" below to link and migrate it in-app, or migrate it from eBay's own listing tools and re-check sync."
      : undefined;
  // In compact mode the sentence is the only place the status is stated at all, so it
  // leads the tooltip; the eBay hint follows it rather than replacing it.
  const title = compact
    ? [sentence, ebayNotFoundHint].filter(Boolean).join(" — ")
    : ebayNotFoundHint;
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${colorClass}`} title={title}>
      {text}
    </span>
  );
}
