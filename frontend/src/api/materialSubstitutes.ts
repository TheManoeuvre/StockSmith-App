import { api } from "./client";
import type {
  MaterialSubstitute,
  MaterialSubstituteInput,
  MaterialSubstituteUpdateInput,
  MaterialSubstituteUsage,
  MaterialSubstituteUsageInput,
} from "./types";

export const materialSubstitutesApi = {
  list: (materialId: number) => api.get<MaterialSubstitute[]>(`/materials/${materialId}/substitutes`),
  add: (materialId: number, input: MaterialSubstituteInput) =>
    api.post<MaterialSubstitute>(`/materials/${materialId}/substitutes`, input),
  update: (materialId: number, substituteId: number, input: MaterialSubstituteUpdateInput) =>
    api.patch<MaterialSubstitute>(`/materials/${materialId}/substitutes/${substituteId}`, input),
};

export const materialSubstituteUsageApi = {
  create: (input: MaterialSubstituteUsageInput) =>
    api.post<MaterialSubstituteUsage>("/material-substitute-usage", input),
  list: (params: { material_id?: number; order_id?: number; build_id?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.material_id != null) qs.set("material_id", String(params.material_id));
    if (params.order_id != null) qs.set("order_id", String(params.order_id));
    if (params.build_id != null) qs.set("build_id", String(params.build_id));
    const suffix = qs.toString();
    return api.get<MaterialSubstituteUsage[]>(`/material-substitute-usage${suffix ? `?${suffix}` : ""}`);
  },
};
