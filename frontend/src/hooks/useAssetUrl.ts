import { useEffect, useState } from "react";
import { assetThumbnailUrl, platformFetch } from "../api/client";

// Module-level cache: assets are never replaced in place (only deleted and re-uploaded
// as a new id), so a plain id-keyed cache is safe and persists across remounts/navigation
// for the lifetime of the app session — avoids re-fetching the same thumbnail every time.
const cache = new Map<number, string>();

/** Fetches an asset's thumbnail bytes with the auth header and exposes a blob: URL for <img>/<a> use. */
export function useAssetUrl(assetId: number | null): string | null {
  const [blobUrl, setBlobUrl] = useState<string | null>(assetId !== null ? (cache.get(assetId) ?? null) : null);

  useEffect(() => {
    if (assetId === null) {
      setBlobUrl(null);
      return;
    }
    const cached = cache.get(assetId);
    if (cached) {
      setBlobUrl(cached);
      return;
    }
    let cancelled = false;

    (async () => {
      const { url, headers } = await assetThumbnailUrl(assetId);
      const response = await platformFetch(url, { headers });
      if (!response.ok || cancelled) return;
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      cache.set(assetId, objectUrl);
      if (!cancelled) setBlobUrl(objectUrl);
    })();

    return () => {
      cancelled = true;
    };
  }, [assetId]);

  return blobUrl;
}
