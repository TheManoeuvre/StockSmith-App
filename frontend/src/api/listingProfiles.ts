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

// Etsy's own vocabulary, sent as-is. Not modelled as a local enum: these are Etsy's
// values, they change on Etsy's schedule, and a new one shouldn't need a release here.
export const ETSY_WHO_MADE = ["i_did", "someone_else", "collective"];
export const ETSY_WHEN_MADE = [
  "made_to_order",
  "2020_2026",
  "2010_2019",
  "2007_2009",
  "before_2007",
];

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
};
