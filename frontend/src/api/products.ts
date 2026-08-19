import { api, downloadCsv, uploadCsv, type CsvImportResult } from "./client";
import type {
  ABCClass,
  BomLine,
  BomLineRead,
  Build,
  BulkBomAmendRequest,
  BulkBomAmendResult,
  BundleItem,
  BundleItemRead,
  KittingBomLine,
  KittingBomLineRead,
  PricingMode,
  Product,
  ProductPage,
  ProductPriceSnapshot,
  ProductStockEvent,
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
  push_buildable_capacity?: boolean;
  pricing_mode?: PricingMode;
  pricing_variable_attribute?: number | null;
  product_category_id?: number | null;
  /** Null means "inherit" for both — the backend resolves through the product category then the
   * shop-wide default (services/abc.py). */
  abc_class?: ABCClass | null;
  stock_take_interval_days?: number | null;
}

// Several pickers (bundle items, manual order lines, unmapped-SKU mapping) need the
// whole catalog as a flat dropdown, not one page of it — `list()` below requests it in
// a single call via a generously large limit rather than a separate unpaginated
// endpoint. Matches the `le` bound on the backend's `GET /products` limit param.
const ALL_PRODUCTS_LIMIT = 10000;

export const productsApi = {
  list: () => api.get<ProductPage>(`/products?limit=${ALL_PRODUCTS_LIMIT}&offset=0`).then((page) => page.items),
  listPaged: (limit: number, offset: number, productCategoryId?: number | null) =>
    api.get<ProductPage>(
      // Filtered server-side: the list is paginated, so narrowing it client-side would
      // filter only the current page and leave the total wrong.
      `/products?limit=${limit}&offset=${offset}${productCategoryId != null ? `&product_category_id=${productCategoryId}` : ""}`,
    ),
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
  // Defaults to a preview server-side — pass apply: true only after the user has seen it.
  amendVariantBomOverrides: (id: number, payload: BulkBomAmendRequest) =>
    api.post<BulkBomAmendResult>(`/products/${id}/variants/bom-overrides/amend`, payload),
  listBuilds: (id: number) => api.get<Build[]>(`/products/${id}/builds`),
  listStockAdjustments: (id: number) => api.get<StockAdjustment[]>(`/products/${id}/stock-adjustments`),
  listStockHistory: (id: number) => api.get<ProductStockEvent[]>(`/products/${id}/stock-history`),
  getBundleItems: (id: number) => api.get<BundleItemRead[]>(`/products/${id}/bundle-items`),
  replaceBundleItems: (id: number, items: BundleItem[]) =>
    api.put<BundleItemRead[]>(`/products/${id}/bundle-items`, items),
  getPriceHistory: (id: number) => api.get<ProductPriceSnapshot[]>(`/products/${id}/price-history`),
  exportCsv: () => downloadCsv("/products/export", "products.csv"),
  importCsv: (fileBytes: Uint8Array, filename: string): Promise<CsvImportResult> =>
    uploadCsv("/products/import", fileBytes, filename),
};

export const buildsApi = {
  create: (input: {
    product_id: number;
    variant_id?: number | null;
    qty_built: number;
    qty_failed?: number;
    failed_consumption?: Record<number, boolean> | null;
    notes?: string | null;
  }) => api.post<Build>("/builds", input),
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
