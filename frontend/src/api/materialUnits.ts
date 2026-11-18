import { api } from "./client";
import type { MaterialUnitRow } from "./types";

export const materialUnitsApi = {
  list: () => api.get<MaterialUnitRow[]>("/material-units"),
  findOrCreate: (name: string) => api.post<MaterialUnitRow>("/material-units/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<MaterialUnitRow>(`/material-units/${id}`, input),
  remove: (id: number) => api.delete<void>(`/material-units/${id}`),
  // Absolute positions in one request, as for material categories.
  reorder: (ids: number[]) => api.post<MaterialUnitRow[]>("/material-units/reorder", { ids }),
};
