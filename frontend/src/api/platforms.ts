import { api } from "./client";
import type { ListingPlatform } from "./types";

export interface PlatformStatus {
  connected: boolean;
  account_id: string | null;
  shop_name: string | null;
  has_shop_icon: boolean;
  scopes: string | null;
  connected_at: string | null;
  sync_start_date: string | null;
  last_orders_synced_at: string | null;
  last_refreshed_at: string | null;
}

export interface SyncPreviewLine {
  external_line_id: string;
  sku: string | null;
  qty: number;
  matched_product_id: number | null;
  matched_product_name: string | null;
  matched_variant_id: number | null;
  matched_variant_name: string | null;
}

export interface SyncPreviewOrder {
  external_order_id: string;
  buyer_name: string | null;
  placed_at: string;
  is_cancelled: boolean;
  is_shipped: boolean;
  already_imported: boolean;
  lines: SyncPreviewLine[];
  raw: unknown;
}

export interface SyncPreviewResult {
  fetched_count: number;
  new_count: number;
  needs_mapping_count: number;
  orders: SyncPreviewOrder[];
}

export interface SyncCommitResult {
  fetched_count: number;
  created_count: number;
  updated_count: number;
  needs_mapping_count: number;
  shipped_count: number;
  order_ids: number[];
}

export interface SyncRunRead {
  id: number;
  platform: string;
  mode: "preview" | "commit";
  status: "success" | "error";
  started_at: string;
  finished_at: string | null;
  fetched_count: number;
  new_count: number;
  needs_mapping_count: number;
  shipped_count: number;
  error_message: string | null;
}

export interface SyncRunPage {
  items: SyncRunRead[];
  total: number;
}

export type ListingSyncStatus = "synced" | "listing_not_active" | "not_found" | "not_tested";
export type ProductSyncStatus = "synced" | "partial" | "not_found" | "not_tested";

export interface UnitSyncResult {
  variant_id: number | null;
  variant_name: string | null;
  sku: string | null;
  status: ListingSyncStatus;
  external_listing_id: string | null;
  external_title: string | null;
  external_variation: string | null;
  external_state: string | null;
  external_quantity: number | null;
  last_checked_at: string | null;
}

export interface ProductListingSyncSummary {
  product_id: number;
  product_status: ProductSyncStatus;
  units: UnitSyncResult[];
}

export interface BulkListingSyncResult {
  summaries: ProductListingSyncSummary[];
  synced_count: number;
  partial_count: number;
  not_found_count: number;
}

export const platformsApi = {
  status: (platform: ListingPlatform) => api.get<PlatformStatus>(`/platforms/${platform}/status`),
  connect: (platform: ListingPlatform) => api.post<{ authorize_url: string }>(`/platforms/${platform}/connect`),
  disconnect: (platform: ListingPlatform) => api.post<void>(`/platforms/${platform}/disconnect`),
  previewSync: (platform: ListingPlatform) => api.post<SyncPreviewResult>(`/platforms/${platform}/preview-sync`),
  syncOrders: (platform: ListingPlatform) => api.post<SyncCommitResult>(`/platforms/${platform}/sync-orders`),
  syncLog: (platform: ListingPlatform, limit: number, offset: number) =>
    api.get<SyncRunPage>(`/platforms/${platform}/sync-log?limit=${limit}&offset=${offset}`),
  checkProductSync: (platform: ListingPlatform, productId: number) =>
    api.post<ProductListingSyncSummary>(`/platforms/${platform}/products/${productId}/check-sync`),
  getProductSyncStatus: (platform: ListingPlatform, productId: number) =>
    api.get<ProductListingSyncSummary>(`/platforms/${platform}/products/${productId}/sync-status`),
  checkAllListings: (platform: ListingPlatform) =>
    api.post<BulkListingSyncResult>(`/platforms/${platform}/check-all-listings`),
  getAllSyncStatus: (platform: ListingPlatform) =>
    api.get<Record<number, ProductSyncStatus>>(`/platforms/${platform}/all-sync-status`),
  updateSyncStartDate: (platform: ListingPlatform, syncStartDate: string) =>
    api.patch<PlatformStatus>(`/platforms/${platform}/sync-start-date`, { sync_start_date: syncStartDate }),
};
