import { useEffect, useState } from "react";
import { platformFetch, supplierLogoUrl } from "../api/client";

// Keyed on updatedAt as well as the id: the logo file keeps one name on disk, so replacing it
// doesn't change the URL. The backend bumps logo_updated_at on every change, which makes the key
// go stale exactly when the file does. Same approach as useMaterialImageUrl.
const cache = new Map<string, string>();

/** Fetches a supplier's logo with the auth header and exposes a blob: URL. Null while loading,
 *  when the supplier has no logo (updatedAt null), or if the fetch fails. */
export function useSupplierLogoUrl(supplierId: number | null, updatedAt: string | null): string | null {
  const key = supplierId !== null && updatedAt !== null ? `${supplierId}-${updatedAt}` : null;
  const [blobUrl, setBlobUrl] = useState<string | null>(key !== null ? (cache.get(key) ?? null) : null);

  useEffect(() => {
    if (supplierId === null || key === null) {
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
      try {
        const { url, headers } = await supplierLogoUrl(supplierId);
        const response = await platformFetch(url, { headers });
        if (!response.ok || cancelled) return;
        const objectUrl = URL.createObjectURL(await response.blob());
        cache.set(key, objectUrl);
        if (!cancelled) setBlobUrl(objectUrl);
      } catch {
        // A logo is decoration: failing to load one must never break the page around it.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [supplierId, key]);

  return blobUrl;
}
