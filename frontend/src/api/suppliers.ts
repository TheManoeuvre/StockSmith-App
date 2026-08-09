import { api } from "./client";
import type { Supplier } from "./types";

export const suppliersApi = {
  list: () => api.get<Supplier[]>("/suppliers"),
  findOrCreate: (name: string) => api.post<Supplier>("/suppliers/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<Supplier>(`/suppliers/${id}`, input),
  remove: (id: number) => api.delete<void>(`/suppliers/${id}`),
  merge: (id: number, targetId: number) => api.post<Supplier>(`/suppliers/${id}/merge`, { target_id: targetId }),
};
