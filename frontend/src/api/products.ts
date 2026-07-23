import { api, downloadCsv, uploadCsv, type CsvImportResult } from "./client";
import type {
  BomLine,
  BomLineRead,
  Build,
  BundleItem,
  BundleItemRead,
  KittingBomLine,
  KittingBomLineRead,
  PricingMode,
  Product,
  ProductPriceSnapshot,
  StockAdjustment,
  Variant,
  VariantAttributeSpec,
} from "./types";

export interface ProductInput {
  name: string;
  sku?: string | null;
  description?: string | null;
  barcode?: string | null;
  is_bundle?: boolean;
  sale_price?: string | null;
  shipping_profile_id?: number | null;
  platform_fee_percent?: string | null;
  platform_ceiling_qty?: number | null;
  pricing_mode?: PricingMode;
  pricing_variable_attribute?: number | null;
}

export const productsApi = {
  list: () => api.get<Product[]>("/products"),
  get: (id: number) => api.get<Product>(`/products/${id}`),
  create: (input: ProductInput) => api.post<Product>("/products", input),
  update: (id: number, input: Partial<ProductInput> & { is_active?: boolean }) =>
    api.patch<Product>(`/products/${id}`, input),
  remove: (id: number) => api.delete<void>(`/products/${id}`),
  getBom: (id: number) => api.get<BomLineRead[]>(`/products/${id}/bom`),
  replaceBom: (id: number, lines: BomLine[]) => api.put<BomLineRead[]>(`/products/${id}/bom`, lines),
  getKittingBom: (id: number) => api.get<KittingBomLineRead[]>(`/products/${id}/kitting-bom`),
  replaceKittingBom: (id: number, lines: KittingBomLine[]) =>
    api.put<KittingBomLineRead[]>(`/products/${id}/kitting-bom`, lines),
  listVariants: (id: number) => api.get<Variant[]>(`/products/${id}/variants`),
  createVariant: (id: number, input: { variant_name: string; sku_suffix?: string | null }) =>
    api.post<Variant>(`/products/${id}/variants`, input),
  generateVariants: (id: number, attributes: VariantAttributeSpec[]) =>
    api.post<Variant[]>(`/products/${id}/variants/generate`, { attributes }),
  listBuilds: (id: number) => api.get<Build[]>(`/products/${id}/builds`),
  listStockAdjustments: (id: number) => api.get<StockAdjustment[]>(`/products/${id}/stock-adjustments`),
  getBundleItems: (id: number) => api.get<BundleItemRead[]>(`/products/${id}/bundle-items`),
  replaceBundleItems: (id: number, items: BundleItem[]) =>
    api.put<BundleItemRead[]>(`/products/${id}/bundle-items`, items),
  getPriceHistory: (id: number) => api.get<ProductPriceSnapshot[]>(`/products/${id}/price-history`),
  exportCsv: () => downloadCsv("/products/export", "products.csv"),
  importCsv: (fileBytes: Uint8Array, filename: string): Promise<CsvImportResult> =>
    uploadCsv("/products/import", fileBytes, filename),
};

export const buildsApi = {
  create: (input: { product_id: number; variant_id?: number | null; qty_built: number; notes?: string | null }) =>
    api.post<Build>("/builds", input),
};

export const stockAdjustmentsApi = {
  create: (input: {
    product_id: number;
    variant_id?: number | null;
    mode: "adjust" | "set";
    value: number;
    reason: string;
  }) => api.post<StockAdjustment>("/stock-adjustments", input),
};
