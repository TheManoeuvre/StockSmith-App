import { api } from "./client";
import type { ListingPlatform } from "./types";

export interface ListingProfile {
  id: number;
  platform: ListingPlatform;
  name: string;
  is_default: boolean;

  etsy_taxonomy_id: number | null;
  etsy_who_made: string | null;
  etsy_when_made: string | null;
  etsy_is_supply: boolean | null;
  etsy_shipping_profile_id: number | null;
  etsy_return_policy_id: number | null;
  etsy_shop_section_id: number | null;
  etsy_processing_min: number | null;
  etsy_processing_max: number | null;

  ebay_category_id: string | null;
  ebay_condition: string | null;
  ebay_fulfillment_policy_id: string | null;
  ebay_payment_policy_id: string | null;
  ebay_return_policy_id: string | null;
  ebay_merchant_location_key: string | null;
  ebay_marketplace_id: string | null;
}

export type ListingProfileWrite = Partial<Omit<ListingProfile, "id" | "platform">> & { name?: string };

export interface ProductPlatformSettings {
  product_id: number;
  platform: ListingPlatform;
  listing_profile_id: number | null;
  is_target: boolean | null;
  listing_title: string | null;
  listing_description: string | null;
  // What the listing would actually carry once the fallback chain is applied, and where
  // each value came from — so the editor can say "inherited from the product name" rather
  // than presenting a fallback as though someone authored it.
  resolved_title: string;
  resolved_title_source: string;
  resolved_description: string | null;
  resolved_description_source: string;
}

export interface ReadinessIssue {
  field: string;
  severity: "blocker" | "warning";
  message: string;
  fix_hint: string | null;
}

export interface DraftReadinessReport {
  product_id: number;
  platform: ListingPlatform;
  can_create: boolean;
  profile_id: number | null;
  profile_name: string | null;
  title: string;
  title_source: string;
  description_chars: number;
  unit_count: number;
  priced_unit_count: number;
  image_count: number;
  issues: ReadinessIssue[];
}

export interface NamedOption {
  id: string;
  label: string;
}

export interface TaxonomyNode {
  id: number;
  name: string;
  // Leaf names repeat across Etsy's tree — several nodes are called "Stands", and only the
  // ancestry tells them apart.
  path: string;
  level: number;
}

export const listingProfilesApi = {
  list: (platform: ListingPlatform) =>
    api.get<ListingProfile[]>(`/settings/listing-profiles/${platform}`),
  create: (platform: ListingPlatform, payload: ListingProfileWrite) =>
    api.post<ListingProfile>(`/settings/listing-profiles/${platform}`, payload),
  update: (platform: ListingPlatform, id: number, payload: ListingProfileWrite) =>
    api.patch<ListingProfile>(`/settings/listing-profiles/${platform}/${id}`, payload),
  remove: (platform: ListingPlatform, id: number) =>
    api.delete<void>(`/settings/listing-profiles/${platform}/${id}`),

  getProductSettings: (platform: ListingPlatform, productId: number) =>
    api.get<ProductPlatformSettings>(`/platforms/${platform}/products/${productId}/settings`),
  saveProductSettings: (
    platform: ListingPlatform,
    productId: number,
    payload: Partial<ProductPlatformSettings>
  ) => api.put<ProductPlatformSettings>(`/platforms/${platform}/products/${productId}/settings`, payload),
  draftReadiness: (platform: ListingPlatform, productId: number) =>
    api.get<DraftReadinessReport>(`/platforms/${platform}/products/${productId}/draft-readiness`),

  // Etsy identifies these by numeric id and surfaces none of those ids in its own seller
  // UI, so the only alternative to these lookups is asking the user to read an API response.
  searchEtsyTaxonomy: (search: string) =>
    api.get<TaxonomyNode[]>(`/platforms/etsy/taxonomy?search=${encodeURIComponent(search)}`),
  etsyTaxonomyNode: (id: number) => api.get<TaxonomyNode>(`/platforms/etsy/taxonomy/${id}`),
  etsyShippingProfiles: () => api.get<NamedOption[]>(`/platforms/etsy/shipping-profiles`),
  etsyReturnPolicies: () => api.get<NamedOption[]>(`/platforms/etsy/return-policies`),
};
