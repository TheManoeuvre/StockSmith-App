import { api } from "./client";

export type BackfillField = "description" | "price" | "image";

export interface VariantPriceProposal {
  variant_id: number;
  variant_name: string;
  sku: string;
  proposed_price: string;
}

// Every field is null when there is nothing to take — either Etsy had no value, or
// StockSmith already holds one and is never overwritten.
export interface ProductBackfillProposal {
  product_id: number;
  product_name: string;
  external_listing_id: string;
  listing_title: string | null;
  description: string | null;
  description_chars: number;
  sale_price: string | null;
  image_url: string | null;
  variant_prices: VariantPriceProposal[];
}

export interface EtsyBackfillPreview {
  products: ProductBackfillProposal[];
  already_complete: number;
  unmatched: number;
}

export interface EtsyBackfillResult {
  products_updated: number;
  descriptions_filled: number;
  prices_filled: number;
  images_filled: number;
  errors: string[];
}

export const etsyBackfillApi = {
  preview: () => api.get<EtsyBackfillPreview>(`/platforms/etsy/backfill-preview`),
  apply: (items: { product_id: number; fields: BackfillField[] }[]) =>
    api.post<EtsyBackfillResult>(`/platforms/etsy/backfill`, { items }),
};

export interface ProfileProposal {
  index: number;
  suggested_name: string;
  is_complete: boolean;
  product_count: number;
  product_names: string[];
  taxonomy_id: number | null;
  who_made: string | null;
  when_made: string | null;
  is_supply: boolean | null;
  shipping_profile_id: number | null;
  return_policy_id: number | null;
  processing_min: number | null;
  processing_max: number | null;
}

export interface ApplyProfileProposalsResult {
  profiles_created: number;
  products_assigned: number;
}

export const etsyProfileProposalsApi = {
  preview: () => api.get<{ proposals: ProfileProposal[] }>(`/platforms/etsy/profile-proposals`),
  apply: (items: { index: number; name: string }[], assignProducts = true) =>
    api.post<ApplyProfileProposalsResult>(`/platforms/etsy/profile-proposals/apply`, {
      items,
      assign_products: assignProducts,
    }),
};
