import { api, downloadCsv, materialImageUploadUrl, uploadCsv, type CsvImportResult } from "./client";
import { getSettings, uploadFile } from "../lib/tauri";
import type { Material, MaterialCategory, MaterialStockHistoryEntry, MaterialUnit, Purchase } from "./types";

export interface MaterialInput {
  name: string;
  category: MaterialCategory;
  unit: MaterialUnit;
  reorder_threshold: string;
  colour?: string | null;
  material_type_id?: number | null;
  barcode?: string | null;
  manufacturer_id?: number | null;
  default_supplier_id?: number | null;
  typical_reorder_qty?: string | null;
  product_url?: string | null;
}

export const materialsApi = {
  list: () => api.get<Material[]>("/materials"),
  get: (id: number) => api.get<Material>(`/materials/${id}`),
  create: (input: MaterialInput) => api.post<Material>("/materials", input),
  update: (id: number, input: Partial<MaterialInput> & { is_active?: boolean }) =>
    api.patch<Material>(`/materials/${id}`, input),
  remove: (id: number) => api.delete<void>(`/materials/${id}`),
  listColours: () => api.get<string[]>("/materials/colours"),
  listByType: (materialTypeId: number) => api.get<Material[]>(`/materials?material_type_id=${materialTypeId}`),
  getStockHistory: (id: number, limit?: number) =>
    api.get<MaterialStockHistoryEntry[]>(
      `/materials/${id}/stock-history${limit != null ? `?limit=${limit}` : ""}`
    ),
  adjust: (id: number, mode: "adjust" | "set", value: string, reason: string) =>
    api.post<Material>(`/materials/${id}/adjustments`, { mode, value, reason }),
  uploadImage: async (
    materialId: number,
    filePath: string,
    originalFilename: string,
    onProgress?: (loaded: number, total: number) => void
  ): Promise<void> => {
    const { sharedPassword } = await getSettings();
    const url = await materialImageUploadUrl(materialId);
    const params = new URLSearchParams({ original_filename: originalFilename });
    await uploadFile(
      `${url}?${params.toString()}`,
      filePath,
      { Authorization: `Bearer ${sharedPassword ?? ""}`, "Content-Type": "application/octet-stream" },
      onProgress
    );
  },
  removeImage: (materialId: number) => api.delete<void>(`/materials/${materialId}/image`),
  importImageUrl: (materialId: number, url: string) =>
    api.post<Material>(`/materials/${materialId}/image/import-url`, { url }),
  createDraftPurchase: (materialId: number, qty?: string | null) =>
    api.post<Purchase>(`/materials/${materialId}/draft-purchase`, { qty: qty || null }),
  exportCsv: () => downloadCsv("/materials/export", "materials.csv"),
  importCsv: (fileBytes: Uint8Array, filename: string): Promise<CsvImportResult> =>
    uploadCsv("/materials/import", fileBytes, filename),
};
