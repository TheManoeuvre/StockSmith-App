import { api } from "./client";
import type { MaterialType } from "./types";

export const materialTypesApi = {
  list: () => api.get<MaterialType[]>("/material-types"),
  findOrCreate: (name: string) => api.post<MaterialType>("/material-types/find-or-create", { name }),
};
