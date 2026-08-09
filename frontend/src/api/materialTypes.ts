import { api } from "./client";
import type { MaterialType } from "./types";

export const materialTypesApi = {
  list: () => api.get<MaterialType[]>("/material-types"),
  findOrCreate: (name: string) => api.post<MaterialType>("/material-types/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<MaterialType>(`/material-types/${id}`, input),
  remove: (id: number) => api.delete<void>(`/material-types/${id}`),
  merge: (id: number, targetId: number) => api.post<MaterialType>(`/material-types/${id}/merge`, { target_id: targetId }),
};
