import { PlatformSyncBadge } from "stocksmith-ui";

/** One listing's sync state per platform — the full sentence, as in a product's Stores section. */
export const ListingStatuses = () => (
  <div className="flex flex-col items-start gap-2">
    <PlatformSyncBadge platform="etsy" status="synced" />
    <PlatformSyncBadge platform="etsy" status="listing_not_active" />
    <PlatformSyncBadge platform="ebay" status="not_found" />
    <PlatformSyncBadge platform="ebay" status="not_tested" />
  </div>
);

/** Product-level roll-ups: "partial" means only some variants are linked. */
export const ProductStatuses = () => (
  <div className="flex flex-col items-start gap-2">
    <PlatformSyncBadge platform="ebay" status="synced" />
    <PlatformSyncBadge platform="etsy" status="partial" />
    <PlatformSyncBadge platform="shopify" status="not_tested" />
  </div>
);

/** Compact: just the platform name, for the products list's Stores column (the sentence moves to the tooltip). */
export const Compact = () => (
  <div className="flex items-center gap-1.5">
    <PlatformSyncBadge platform="etsy" status="synced" compact />
    <PlatformSyncBadge platform="ebay" status="partial" compact />
    <PlatformSyncBadge platform="shopify" status="not_tested" compact />
  </div>
);
