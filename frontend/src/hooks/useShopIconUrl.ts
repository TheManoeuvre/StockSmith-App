import { useEffect, useState } from "react";
import { platformFetch, shopIconUrl } from "../api/client";

// Cache key includes connected_at because a shop's icon is always saved to the same
// fixed icon.<ext> path on disk (per save_platform_icon convention) — reconnecting
// doesn't change the URL, so the platform alone isn't enough to detect staleness.
// connected_at is bumped by the backend on every (re)connect, so keying on both
// naturally invalidates the cache exactly when the underlying file actually changed.
const cache = new Map<string, string>();

function cacheKey(platform: string, connectedAt: string): string {
  return `${platform}-${connectedAt}`;
}

/** Fetches a connected platform's shop icon bytes with the auth header and exposes a blob: URL. */
export function useShopIconUrl(
  platform: string,
  hasIcon: boolean,
  connectedAt: string | null
): string | null {
  const key = hasIcon && connectedAt !== null ? cacheKey(platform, connectedAt) : null;
  const [blobUrl, setBlobUrl] = useState<string | null>(key !== null ? (cache.get(key) ?? null) : null);

  useEffect(() => {
    if (key === null) {
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
      const { url, headers } = await shopIconUrl(platform);
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
  }, [platform, key]);

  return blobUrl;
}
