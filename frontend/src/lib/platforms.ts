import type { ListingPlatform } from "../api/types";

// Single source of truth for how a platform's name/colour is displayed anywhere in the
// app — never print a raw ListingPlatform enum value ("etsy"/"ebay") directly.
export const PLATFORM_LABELS: Record<ListingPlatform, string> = {
  etsy: "Etsy",
  ebay: "eBay",
  shopify: "Shopify",
};

export const PLATFORM_COLORS: Record<ListingPlatform, { solid: string; muted: string }> = {
  etsy: { solid: "bg-orange-600 text-white", muted: "bg-orange-50 text-orange-700 border border-orange-200" },
  ebay: { solid: "bg-blue-600 text-white", muted: "bg-blue-50 text-blue-700 border border-blue-200" },
  shopify: { solid: "bg-emerald-600 text-white", muted: "bg-emerald-50 text-emerald-700 border border-emerald-200" },
};
