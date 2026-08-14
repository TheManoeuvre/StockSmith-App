import { api } from "./client";
import type { ListingPlatform } from "./types";

export type LimitField =
  | "sku_max_length"
  | "title_max_length"
  | "title_charset"
  | "description_max_length"
  | "variation_attribute_max_count"
  | "variation_max_count"
  | "attribute_name_max_length"
  | "attribute_value_max_length"
  | "attribute_value_charset"
  | "image_max_count"
  | "price_decimal_places"
  | "quantity_max";

export type ViolationSeverity = "blocker" | "warning";

// `message` is the complete sentence from the backend, which already names the value, the
// limit and the platform that imposes it. Render it as-is rather than reassembling one
// from the parts — the same wording then appears wherever a violation surfaces.
export interface FieldViolation {
  field: LimitField;
  severity: ViolationSeverity;
  current_value: string;
  current_length: number | null;
  limit: string;
  imposed_by: ListingPlatform;
  message: string;
  suggested_value: string | null;
}

export interface UnitCompatibility {
  variant_id: number | null;
  variant_name: string | null;
  sku: string | null;
  violations: FieldViolation[];
}

export interface ProductCompatibility {
  product_id: number;
  product_name: string;
  product_sku: string | null;
  is_blocked: boolean;
  violations: FieldViolation[];
  units: UnitCompatibility[];
}

// `products` holds only the entries with something to report; `total_products` is the
// whole active catalogue, so "3 of 26" needs no second call.
export interface CatalogueCompatibilityReport {
  platform: ListingPlatform;
  total_products: number;
  blocked_count: number;
  warning_count: number;
  products: ProductCompatibility[];
}

export const platformLimitsApi = {
  catalogueCompatibility: (platform: ListingPlatform) =>
    api.get<CatalogueCompatibilityReport>(`/platforms/${platform}/catalogue-compatibility`),
};
