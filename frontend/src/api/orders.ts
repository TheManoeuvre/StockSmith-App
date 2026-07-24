import { api } from "./client";
import type { Order, OrderKittingOverrideLine, OrderKittingSummary, OrderStatus } from "./types";

export type ReturnDisposition = "scrap" | "return_to_stock";

export interface CancellationKittingMaterial {
  material_id: number;
  material_name: string;
  qty_per_unit: string;
}

export interface CancellationLineOption {
  order_line_id: number;
  product_id: number | null;
  variant_id: number | null;
  product_name: string | null;
  variant_name: string | null;
  pending_qty: number;
  shipped_qty: number;
  default_product_disposition: ReturnDisposition;
  kitting_materials: CancellationKittingMaterial[];
  default_kitting_disposition: ReturnDisposition;
}

export interface CancellationPreview {
  order_id: number;
  already_cancelled: boolean;
  lines: CancellationLineOption[];
}

export interface LineCancellationDecision {
  order_line_id: number;
  product_disposition: ReturnDisposition;
  kitting_disposition?: ReturnDisposition;
}

export interface OrderCancelRequest {
  line_decisions: LineCancellationDecision[];
  reason?: string | null;
}

export interface OrderLineInput {
  product_id?: number | null;
  variant_id?: number | null;
  ordered_qty: number;
  unit_price?: string | null;
  currency?: string | null;
}

export interface OrderCreateInput {
  buyer_name?: string | null;
  buyer_note?: string | null;
  notes?: string | null;
  currency?: string | null;
  shipping_profile_id?: number | null;
  shipping_charged?: string | null;
  lines: OrderLineInput[];
}

export interface OrderUpdateInput {
  buyer_name?: string | null;
  buyer_note?: string | null;
  notes?: string | null;
  shipping_profile_id?: number | null;
  shipping_charged?: string | null;
}

export const ordersApi = {
  list: (status?: OrderStatus) => api.get<Order[]>(`/orders${status ? `?status_filter=${status}` : ""}`),
  get: (id: number) => api.get<Order>(`/orders/${id}`),
  create: (input: OrderCreateInput) => api.post<Order>("/orders", input),
  update: (id: number, input: OrderUpdateInput) => api.patch<Order>(`/orders/${id}`, input),
  remove: (id: number) => api.delete<void>(`/orders/${id}`),
  cancellationPreview: (id: number) => api.get<CancellationPreview>(`/orders/${id}/cancellation-preview`),
  cancel: (id: number, payload: OrderCancelRequest) => api.post<Order>(`/orders/${id}/cancel`, payload),
  ship: (id: number) => api.post<Order>(`/orders/${id}/ship`),
  allocate: (id: number) => api.post<Order>(`/orders/${id}/allocate`),
  updateLineQty: (lineId: number, orderedQty: number) =>
    api.patch<Order>(`/orders/lines/${lineId}`, { ordered_qty: orderedQty }),
  unassignLine: (lineId: number, qty: number) => api.post<Order>(`/orders/lines/${lineId}/unassign`, { qty }),
  mapSku: (lineId: number, input: { product_id?: number | null; variant_id?: number | null }) =>
    api.post<Order>(`/orders/lines/${lineId}/map-sku`, input),
  createProductAndMap: (lineId: number, input: { name: string; sku?: string | null }) =>
    api.post<Order>(`/orders/lines/${lineId}/create-product-and-map`, input),
  getKittingOverrides: (id: number) => api.get<OrderKittingSummary>(`/orders/${id}/kitting-overrides`),
  replaceKittingOverrides: (id: number, overrides: OrderKittingOverrideLine[]) =>
    api.put<OrderKittingSummary>(`/orders/${id}/kitting-overrides`, overrides),
};
