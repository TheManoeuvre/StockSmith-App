import { api } from "./client";
import type { ListingPlatform } from "./types";

export type PlatformEnvironment = "production" | "sandbox";

export interface PlatformStatus {
  connected: boolean;
  account_id: string | null;
  shop_name: string | null;
  has_shop_icon: boolean;
  scopes: string | null;
  environment: PlatformEnvironment;
  connected_at: string | null;
  sync_start_date: string | null;
  last_orders_synced_at: string | null;
  last_refreshed_at: string | null;
  auto_sync_enabled: boolean;
  sync_interval_minutes: number;
  last_sync_attempt_at: string | null;
  last_sync_success_at: string | null;
  last_sync_error: string | null;
}

export interface SyncSettingsUpdate {
  auto_sync_enabled?: boolean;
  sync_interval_minutes?: number;
}

export interface ListingPushRead {
  id: number;
  product_id: number | null;
  product_name: string | null;
  variant_id: number | null;
  variant_name: string | null;
  platform: ListingPlatform;
  attempted_qty: number;
  status: "success" | "error";
  error_message: string | null;
  attempted_at: string;
}

export interface ListingPushPage {
  items: ListingPushRead[];
  total: number;
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

export interface PlatformCredential {
  platform: ListingPlatform;
  environment: PlatformEnvironment;
  client_id: string | null;
  client_secret_set: boolean;
  public_base_url: string | null;
  ru_name: string | null;
}

export interface PlatformCredentialWrite {
  client_id?: string;
  client_secret?: string;
  public_base_url?: string;
  ru_name?: string;
}

export const platformsApi = {
  status: (platform: ListingPlatform) => api.get<PlatformStatus>(`/platforms/${platform}/status`),
  connect: (platform: ListingPlatform, environment: PlatformEnvironment = "production") =>
    api.post<{ authorize_url: string }>(`/platforms/${platform}/connect?environment=${environment}`),
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
  updateSyncSettings: (platform: ListingPlatform, payload: SyncSettingsUpdate) =>
    api.patch<PlatformStatus>(`/platforms/${platform}/sync-settings`, payload),
  listingPushLog: (platform: ListingPlatform, limit: number, offset: number) =>
    api.get<ListingPushPage>(`/platforms/${platform}/listing-push-log?limit=${limit}&offset=${offset}`),
  getCredentials: (platform: ListingPlatform, environment: PlatformEnvironment = "production") =>
    api.get<PlatformCredential>(`/platforms/${platform}/credentials?environment=${environment}`),
  updateCredentials: (
    platform: ListingPlatform,
    payload: PlatformCredentialWrite,
    environment: PlatformEnvironment = "production"
  ) => api.patch<PlatformCredential>(`/platforms/${platform}/credentials?environment=${environment}`, payload),
};
