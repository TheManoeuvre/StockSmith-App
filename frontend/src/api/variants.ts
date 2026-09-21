import { api } from "./client";
import type {
  PlatformConflictResolution,
  Variant,
  VariantBomLine,
  VariantKittingBomLine,
  VariantMergePlan,
  VariantMergeRequest,
  VariantMergeResult,
} from "./types";

export const variantsApi = {
  get: (id: number) => api.get<Variant>(`/variants/${id}`),
  update: (
    id: number,
    input: {
      variant_name?: string;
      sku_suffix?: string | null;
      is_active?: boolean;
      sale_price?: string | null;
      shipping_profile_id?: number | null;
      platform_fee_percent?: string | null;
      on_platform_conflict?: PlatformConflictResolution;
    }
  ) => api.patch<Variant>(`/variants/${id}`, input),
  // One request for a whole pricing group. Saving a group as one PATCH per variant
  // exhausted the backend's connection pool on products with hundreds of variants.
  updatePricing: (input: {
    variant_ids: number[];
    sale_price: string | null;
    shipping_profile_id: number | null;
    platform_fee_percent: string | null;
  }) => api.patch<void>(`/variants/pricing`, input),
  remove: (id: number) => api.delete<void>(`/variants/${id}`),
  replaceBomOverrides: (id: number, overrides: VariantBomLine[]) =>
    api.put<Variant>(`/variants/${id}/bom-overrides`, overrides),
  replaceKittingBomOverrides: (id: number, overrides: VariantKittingBomLine[]) =>
    api.put<Variant>(`/variants/${id}/kitting-bom-overrides`, overrides),
  // Read-only: what merging `id` into target would do.
  previewMerge: (id: number, targetId: number) =>
    api.post<VariantMergePlan>(`/variants/${id}/merge/preview`, { target_id: targetId }),
  // 409 with code "live_listing_conflicts" until on_live_listing is "proceed".
  merge: (id: number, payload: VariantMergeRequest) => api.post<VariantMergeResult>(`/variants/${id}/merge`, payload),
};
