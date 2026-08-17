import { api } from "./client";
import type { MaterialCategory } from "./types";

export const materialCategoriesApi = {
  list: () => api.get<MaterialCategory[]>("/material-categories"),
  findOrCreate: (name: string) => api.post<MaterialCategory>("/material-categories/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) =>
    api.patch<MaterialCategory>(`/material-categories/${id}`, input),
  remove: (id: number) => api.delete<void>(`/material-categories/${id}`),
  merge: (id: number, targetId: number) =>
    api.post<MaterialCategory>(`/material-categories/${id}/merge`, { target_id: targetId }),
  // Absolute positions in one request rather than a PATCH per row: a reorder is a single
  // intent, and a partial failure part-way through N requests would leave the list in an order
  // nobody asked for.
  reorder: (ids: number[]) => api.post<MaterialCategory[]>("/material-categories/reorder", { ids }),
};
