import { api } from "./client";
import type { ProductType } from "./types";

export const productTypesApi = {
  list: () => api.get<ProductType[]>("/product-types"),
  findOrCreate: (name: string) => api.post<ProductType>("/product-types/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<ProductType>(`/product-types/${id}`, input),
  remove: (id: number) => api.delete<void>(`/product-types/${id}`),
  merge: (id: number, targetId: number) => api.post<ProductType>(`/product-types/${id}/merge`, { target_id: targetId }),
};
