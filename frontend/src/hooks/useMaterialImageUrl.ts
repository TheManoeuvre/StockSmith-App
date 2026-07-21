import { useEffect, useState } from "react";
import { materialImageThumbnailUrl, platformFetch } from "../api/client";

// Cache key includes updatedAt because a material's thumbnail file is always named
// thumb-main.jpg on disk (fixed filename, per material_folder convention) — replacing
// the image doesn't change the URL, so the id alone isn't enough to detect staleness.
// updatedAt is bumped by the backend on every image upload/replace/remove, so keying on
// both naturally invalidates the cache exactly when the underlying file actually changed.
const cache = new Map<string, string>();

function cacheKey(materialId: number, updatedAt: string): string {
  return `${materialId}-${updatedAt}`;
}

/** Fetches a material's thumbnail bytes with the auth header and exposes a blob: URL. */
export function useMaterialImageUrl(materialId: number | null, updatedAt: string | null): string | null {
  const key = materialId !== null && updatedAt !== null ? cacheKey(materialId, updatedAt) : null;
  const [blobUrl, setBlobUrl] = useState<string | null>(key !== null ? (cache.get(key) ?? null) : null);

  useEffect(() => {
    if (materialId === null || updatedAt === null || key === null) {
      setBlobUrl(null);
      return;
    }
    const cached = cache.get(key);
    if (cached) {
      setBlobUrl(cached);
      return;
    }
    let cancelled = false;

    (async () => {
      const { url, headers } = await materialImageThumbnailUrl(materialId);
      const response = await platformFetch(url, { headers });
      if (!response.ok || cancelled) return;
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      cache.set(key, objectUrl);
      if (!cancelled) setBlobUrl(objectUrl);
    })();

    return () => {
      cancelled = true;
    };
  }, [materialId, updatedAt, key]);

  return blobUrl;
}
