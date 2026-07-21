import { api } from "./client";
import type { ListingPlatform } from "./types";

export type FeeBasis = "sale_price" | "sale_price_plus_shipping" | "fees_subtotal";
export type MarginFeeSource = "manual" | "etsy" | "ebay";

export interface PlatformFeeComponent {
  id: number;
  platform: string;
  name: string;
  basis: FeeBasis;
  rate_percent: string | null;
  fixed_amount: string | null;
  display_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PlatformFeeComponentInput {
  name: string;
  basis: FeeBasis;
  rate_percent?: string | null;
  fixed_amount?: string | null;
  display_order?: number;
  enabled?: boolean;
}

export interface MarginFeeConfig {
  fee_source: MarginFeeSource;
}

export const feeConfigApi = {
  getMarginFeeConfig: () => api.get<MarginFeeConfig>("/settings/margin-fee-config"),
  updateMarginFeeConfig: (fee_source: MarginFeeSource) =>
    api.put<MarginFeeConfig>("/settings/margin-fee-config", { fee_source }),
  listFeeComponents: (platform: ListingPlatform) =>
    api.get<PlatformFeeComponent[]>(`/settings/platform-fee-components/${platform}`),
  createFeeComponent: (platform: ListingPlatform, input: PlatformFeeComponentInput) =>
    api.post<PlatformFeeComponent>(`/settings/platform-fee-components/${platform}`, input),
  updateFeeComponent: (platform: ListingPlatform, id: number, input: Partial<PlatformFeeComponentInput>) =>
    api.patch<PlatformFeeComponent>(`/settings/platform-fee-components/${platform}/${id}`, input),
  deleteFeeComponent: (platform: ListingPlatform, id: number) =>
    api.delete<void>(`/settings/platform-fee-components/${platform}/${id}`),
};
