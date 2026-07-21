import { api } from "./client";
import type { Supplier } from "./types";

export const suppliersApi = {
  list: () => api.get<Supplier[]>("/suppliers"),
  findOrCreate: (name: string) => api.post<Supplier>("/suppliers/find-or-create", { name }),
};
