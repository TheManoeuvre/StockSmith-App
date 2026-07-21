import { api } from "./client";
import type { Order, OrderKittingOverrideLine, OrderKittingSummary, OrderStatus } from "./types";

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
  lines: OrderLineInput[];
}

export interface OrderUpdateInput {
  buyer_name?: string | null;
  buyer_note?: string | null;
  notes?: string | null;
}

export const ordersApi = {
  list: (status?: OrderStatus) => api.get<Order[]>(`/orders${status ? `?status_filter=${status}` : ""}`),
  get: (id: number) => api.get<Order>(`/orders/${id}`),
  create: (input: OrderCreateInput) => api.post<Order>("/orders", input),
  update: (id: number, input: OrderUpdateInput) => api.patch<Order>(`/orders/${id}`, input),
  remove: (id: number) => api.delete<void>(`/orders/${id}`),
  cancel: (id: number) => api.post<Order>(`/orders/${id}/cancel`),
  ship: (id: number) => api.post<Order>(`/orders/${id}/ship`),
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
