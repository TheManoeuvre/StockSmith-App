import { api } from "./client";
import type { Colour } from "./types";

export const coloursApi = {
  list: () => api.get<Colour[]>("/colours"),
  findOrCreate: (name: string) => api.post<Colour>("/colours/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<Colour>(`/colours/${id}`, input),
  remove: (id: number) => api.delete<void>(`/colours/${id}`),
  merge: (id: number, targetId: number) => api.post<Colour>(`/colours/${id}/merge`, { target_id: targetId }),
};
