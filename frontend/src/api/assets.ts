import { api, assetUploadUrl } from "./client";
import { getSettings, uploadFile } from "../lib/tauri";
import type { Asset, AssetType } from "./types";

export const assetsApi = {
  list: (productId: number) => api.get<Asset[]>(`/products/${productId}/assets`),
  update: (assetId: number, display_order: number) =>
    api.patch<Asset>(`/assets/${assetId}`, { display_order }),
  remove: (assetId: number) => api.delete<void>(`/assets/${assetId}`),
  upload: async (
    productId: number,
    filePath: string,
    originalFilename: string,
    assetType: AssetType,
    variantId?: number,
    onProgress?: (loaded: number, total: number) => void
  ): Promise<void> => {
    const { sharedPassword } = await getSettings();
    const url = await assetUploadUrl(productId);
    const params = new URLSearchParams({ asset_type: assetType, original_filename: originalFilename });
    if (variantId !== undefined) params.set("variant_id", String(variantId));
    await uploadFile(
      `${url}?${params.toString()}`,
      filePath,
      {
        Authorization: `Bearer ${sharedPassword ?? ""}`,
        "Content-Type": "application/octet-stream",
      },
      onProgress
    );
  },
  importUrl: (productId: number, url: string, assetType: AssetType, variantId?: number) =>
    api.post<Asset>(
      `/products/${productId}/assets/import-url?${new URLSearchParams({
        asset_type: assetType,
        ...(variantId !== undefined ? { variant_id: String(variantId) } : {}),
      })}`,
      { url }
    ),
};
