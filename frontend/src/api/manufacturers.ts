import { api } from "./client";
import type { Manufacturer } from "./types";

export const manufacturersApi = {
  list: () => api.get<Manufacturer[]>("/manufacturers"),
  findOrCreate: (name: string) => api.post<Manufacturer>("/manufacturers/find-or-create", { name }),
};
