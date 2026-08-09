import { api } from "./client";
import type { Manufacturer } from "./types";

export const manufacturersApi = {
  list: () => api.get<Manufacturer[]>("/manufacturers"),
  findOrCreate: (name: string) => api.post<Manufacturer>("/manufacturers/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<Manufacturer>(`/manufacturers/${id}`, input),
  remove: (id: number) => api.delete<void>(`/manufacturers/${id}`),
  merge: (id: number, targetId: number) => api.post<Manufacturer>(`/manufacturers/${id}/merge`, { target_id: targetId }),
};
