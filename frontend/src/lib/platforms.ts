import type { ListingPlatform } from "../api/types";

// Single source of truth for how a platform's name/colour is displayed anywhere in the
// app — never print a raw ListingPlatform enum value ("etsy"/"ebay") directly.
export const PLATFORM_LABELS: Record<ListingPlatform, string> = {
  etsy: "Etsy",
  ebay: "eBay",
  shopify: "Shopify",
  square: "Square",
};

// Only platforms with a real adapter implemented — Shopify is in the ListingPlatform
// enum for future use but has no adapter yet, so it isn't offered anywhere. Square's
// adapter only does order sync (see docs/plan-square-integration.md) — no listing push/
// adoption tools, which is why it's absent from listing-specific UI elsewhere even though
// it's connectable here.
export const CONNECTABLE_PLATFORMS: ListingPlatform[] = ["etsy", "ebay", "square"];

// `accent` is the 3px top border the design canvas gives each store's connection card.
export const PLATFORM_COLORS: Record<ListingPlatform, { solid: string; muted: string; accent: string }> = {
  etsy: {
    solid: "bg-orange-600 text-white",
    muted: "bg-orange-50 text-orange-700 border border-orange-200",
    accent: "border-t-orange-600",
  },
  ebay: {
    solid: "bg-blue-600 text-white",
    muted: "bg-blue-50 text-blue-700 border border-blue-200",
    accent: "border-t-blue-600",
  },
  shopify: {
    solid: "bg-emerald-600 text-white",
    muted: "bg-emerald-50 text-emerald-700 border border-emerald-200",
    accent: "border-t-emerald-600",
  },
  square: {
    solid: "bg-slate-800 text-white",
    muted: "bg-slate-100 text-slate-700 border border-slate-300",
    accent: "border-t-slate-800",
  },
};
