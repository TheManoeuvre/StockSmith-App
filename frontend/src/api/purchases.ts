import { api } from "./client";
import type { Purchase, PurchaseStatus } from "./types";

export interface PurchaseLineInput {
  material_id: number;
  qty: string;
  total_cost: string;
  notes?: string | null;
}

export interface PurchaseCreateInput {
  supplier_id?: number | null;
  order_date?: string | null;
  notes?: string | null;
  lines: PurchaseLineInput[];
}

export interface PurchaseUpdateInput {
  supplier_id?: number | null;
  order_date?: string | null;
  notes?: string | null;
}

export const purchasesApi = {
  list: (status?: PurchaseStatus) => api.get<Purchase[]>(`/purchases${status ? `?status_filter=${status}` : ""}`),
  get: (id: number) => api.get<Purchase>(`/purchases/${id}`),
  create: (input: PurchaseCreateInput) => api.post<Purchase>("/purchases", input),
  update: (id: number, input: PurchaseUpdateInput) => api.patch<Purchase>(`/purchases/${id}`, input),
  replaceLines: (id: number, lines: PurchaseLineInput[]) => api.put<Purchase>(`/purchases/${id}/lines`, lines),
  remove: (id: number) => api.delete<void>(`/purchases/${id}`),
  receive: (id: number) => api.post<Purchase>(`/purchases/${id}/receive`),
  unreceive: (id: number) => api.post<Purchase>(`/purchases/${id}/unreceive`),
};
