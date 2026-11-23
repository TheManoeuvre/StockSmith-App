import { api, supplierLogoUploadUrl } from "./client";
import { getSettings, uploadFile } from "../lib/tauri";
import type { Supplier } from "./types";

export const suppliersApi = {
  list: () => api.get<Supplier[]>("/suppliers"),
  findOrCreate: (name: string) => api.post<Supplier>("/suppliers/find-or-create", { name }),
  update: (id: number, input: Record<string, unknown>) => api.patch<Supplier>(`/suppliers/${id}`, input),
  remove: (id: number) => api.delete<void>(`/suppliers/${id}`),
  merge: (id: number, targetId: number) => api.post<Supplier>(`/suppliers/${id}/merge`, { target_id: targetId }),
  /** Uploads override the website favicon. The file is streamed from disk by the Tauri side. */
  uploadLogo: async (id: number, filePath: string): Promise<void> => {
    const { sharedPassword } = await getSettings();
    await uploadFile(await supplierLogoUploadUrl(id), filePath, {
      Authorization: `Bearer ${sharedPassword ?? ""}`,
      "Content-Type": "application/octet-stream",
    });
  },
  /** Drops the logo; with a website set the backend falls back to its favicon. */
  removeLogo: (id: number) => api.delete<Supplier>(`/suppliers/${id}/logo`),
  refreshFavicon: (id: number) => api.post<Supplier>(`/suppliers/${id}/logo/refresh`),
};
