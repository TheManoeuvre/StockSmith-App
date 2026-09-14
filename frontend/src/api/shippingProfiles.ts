import { api } from "./client";
import type { ListingPlatform, MarketplaceShippingProfile, ShippingProfile } from "./types";

export interface ShippingProfileInput {
  name: string;
  price?: string | null;
  price_etsy?: string | null;
  price_ebay?: string | null;
  cost_etsy?: string | null;
  cost_ebay?: string | null;
  cost_manual?: string | null;
  etsy_shipping_profile_id?: number | null;
  ebay_fulfillment_policy_id?: string | null;
  is_archived?: boolean;
}

export const shippingProfilesApi = {
  /** Active profiles only unless asked otherwise — archived ones stay out of the pickers. */
  list: (includeArchived = false) =>
    api.get<ShippingProfile[]>(`/shipping-profiles${includeArchived ? "?include_archived=true" : ""}`),
  create: (input: ShippingProfileInput) => api.post<ShippingProfile>("/shipping-profiles", input),
  update: (id: number, input: Record<string, unknown>) =>
    api.patch<ShippingProfile>(`/shipping-profiles/${id}`, input),
  remove: (id: number) => api.delete<void>(`/shipping-profiles/${id}`),
  merge: (id: number, targetId: number) =>
    api.post<ShippingProfile>(`/shipping-profiles/${id}/merge`, { target_id: targetId }),
  /** The marketplace's own profiles/policies with their current buyer price. 400 when
   *  that platform is not connected. */
  marketplace: (platform: ListingPlatform) =>
    api.get<MarketplaceShippingProfile[]>(`/shipping-profiles/marketplace/${platform}`),
  /** Pull the linked marketplace profile's buyer price into price_<platform>. */
  importPrice: (id: number, platform: ListingPlatform) =>
    api.post<ShippingProfile>(`/shipping-profiles/${id}/import-price/${platform}`),
};
