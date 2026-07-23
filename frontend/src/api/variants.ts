import { api } from "./client";
import type { Variant, VariantBomLine, VariantKittingBomLine } from "./types";

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
    }
  ) => api.patch<Variant>(`/variants/${id}`, input),
  remove: (id: number) => api.delete<void>(`/variants/${id}`),
  replaceBomOverrides: (id: number, overrides: VariantBomLine[]) =>
    api.put<Variant>(`/variants/${id}/bom-overrides`, overrides),
  replaceKittingBomOverrides: (id: number, overrides: VariantKittingBomLine[]) =>
    api.put<Variant>(`/variants/${id}/kitting-bom-overrides`, overrides),
};
