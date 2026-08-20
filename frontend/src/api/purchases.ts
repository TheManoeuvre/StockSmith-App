import { api } from "./client";
import type { Purchase, PurchaseStatus } from "./types";

export interface PurchaseLineInput {
  /**
   * Present for a line that already exists, absent for a new one. The backend upserts on
   * this: send a line without its id and a purchase that has been received against will
   * refuse the whole save rather than duplicate the line and orphan its deliveries.
   */
  id?: number | null;
  material_id: number;
  qty: string;
  total_cost: string;
  notes?: string | null;
}

export interface PurchaseCreateInput {
  supplier_id?: number | null;
  order_date?: string | null;
  expected_arrival_date?: string | null;
  notes?: string | null;
  lines: PurchaseLineInput[];
}

export interface PurchaseUpdateInput {
  supplier_id?: number | null;
  order_date?: string | null;
  expected_arrival_date?: string | null;
  notes?: string | null;
}

export interface ReceiptLineInput {
  line_id: number;
  qty: string;
  /** Omit to let this delivery take its pro-rata share of the line total. */
  total_cost?: string | null;
}

export interface ReceiptsInput {
  received_at?: string | null;
  notes?: string | null;
  lines: ReceiptLineInput[];
}

export const purchasesApi = {
  list: (status?: PurchaseStatus) => api.get<Purchase[]>(`/purchases${status ? `?status_filter=${status}` : ""}`),
  get: (id: number) => api.get<Purchase>(`/purchases/${id}`),
  create: (input: PurchaseCreateInput) => api.post<Purchase>("/purchases", input),
  update: (id: number, input: PurchaseUpdateInput) => api.patch<Purchase>(`/purchases/${id}`, input),
  replaceLines: (id: number, lines: PurchaseLineInput[]) => api.put<Purchase>(`/purchases/${id}/lines`, lines),
  remove: (id: number) => api.delete<void>(`/purchases/${id}`),

  /** Record one delivery, covering however many lines of the order arrived in it. */
  createReceipts: (id: number, input: ReceiptsInput) => api.post<Purchase>(`/purchases/${id}/receipts`, input),
  /** Undo one delivery. */
  deleteReceiptBatch: (id: number, batchId: string) =>
    api.delete<Purchase>(`/purchases/${id}/receipts?batch_id=${encodeURIComponent(batchId)}`),
  deleteReceipt: (id: number, receiptId: number) => api.delete<Purchase>(`/purchases/${id}/receipts/${receiptId}`),

  /** The rest of this line is never coming. */
  closeLine: (id: number, lineId: number, apportionRemainder: boolean) =>
    api.post<Purchase>(`/purchases/${id}/lines/${lineId}/close`, { apportion_remainder: apportionRemainder }),
  reopenLine: (id: number, lineId: number) => api.post<Purchase>(`/purchases/${id}/lines/${lineId}/reopen`),

  /** Shorthand for "all of it turned up, now" — one click from the list page. */
  receive: (id: number) => api.post<Purchase>(`/purchases/${id}/receive`),
  unreceive: (id: number) => api.post<Purchase>(`/purchases/${id}/unreceive`),
};
