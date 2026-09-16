import type { ShippingProfile } from "../api/types";

export type ShippingChannel = "etsy" | "ebay" | "manual";

/**
 * The postage price a buyer is charged on a given channel — `price_<channel>` when set,
 * else the manual/default `price`. Mirrors backend
 * services/shipping_profiles.py::resolve_shipping_price_for_platform: once a profile is
 * linked to Etsy or eBay, the marketplace's own figure lives in the per-channel column and
 * `price` stays the hand-entered default.
 */
export function shippingPriceForChannel(
  profile: ShippingProfile,
  channel: ShippingChannel | null | undefined
): string {
  if (channel === "etsy" && profile.price_etsy != null) return profile.price_etsy;
  if (channel === "ebay" && profile.price_ebay != null) return profile.price_ebay;
  return profile.price;
}
