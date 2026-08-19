import { api } from "./client";
import type { ProductCategory } from "./types";

export const productCategoriesApi = {
  list: () => api.get<ProductCategory[]>("/product-categories"),
  findOrCreate: (name: string) => api.post<ProductCategory>("/product-categories/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<ProductCategory>(`/product-categories/${id}`, input),
  remove: (id: number) => api.delete<void>(`/product-categories/${id}`),
  merge: (id: number, targetId: number) => api.post<ProductCategory>(`/product-categories/${id}/merge`, { target_id: targetId }),
};
