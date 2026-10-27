import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isUpdateAvailable } from "../lib/appUpdate";
import {
  canUpdate,
  checkForUpdate,
  getIgnoredUpdateVersion,
  getInstalledVersion,
  setIgnoredUpdateVersion,
} from "../lib/updater";

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
// Refocusing the window re-checks only if the last answer is older than this, so alt-tabbing
// back and forth doesn't hit the release feed each time.
const STALE_AFTER_MS = 60 * 60 * 1000;
// The first check waits so it doesn't compete with the backend coming up and the first page
// loading. Opening the About page asks for an immediate one.
const FIRST_CHECK_DELAY_MS = 30 * 1000;

const UPDATE_KEY = ["app-update"] as const;
const IGNORED_KEY = ["app-update-ignored"] as const;
const VERSION_KEY = ["app-installed-version"] as const;

/**
 * Whether a newer release exists, checked on a schedule: once shortly after launch, every six
 * hours while the app is open, and on window focus when the last check is over an hour old. A
 * failed check is silent and retried next cycle — being offline is not an error worth showing.
 *
 * `available` is what the badge and the About page's call to action key off; it is false for a
 * release the user ignored. `latest` is still set in that case so the About page can say so.
 */
export function useAppUpdate({ immediate = false }: { immediate?: boolean } = {}) {
  const queryClient = useQueryClient();
  const [armed, setArmed] = useState(immediate);
  useEffect(() => {
    if (armed) return;
    const timer = setTimeout(() => setArmed(true), FIRST_CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [armed]);

  const installed = useQuery({
    queryKey: VERSION_KEY,
    queryFn: getInstalledVersion,
    staleTime: Infinity,
  });
  const latest = useQuery({
    queryKey: UPDATE_KEY,
    queryFn: checkForUpdate,
    enabled: canUpdate() && armed,
    refetchInterval: CHECK_INTERVAL_MS,
    staleTime: STALE_AFTER_MS,
    refetchOnWindowFocus: true,
    retry: false,
  });
  const ignored = useQuery({
    queryKey: IGNORED_KEY,
    queryFn: getIgnoredUpdateVersion,
    staleTime: Infinity,
  });

  const ignore = useMutation({
    mutationFn: (version: string | null) => setIgnoredUpdateVersion(version),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: IGNORED_KEY }),
  });

  const update = latest.data ?? null;
  return {
    installedVersion: installed.data ?? null,
    update,
    ignoredVersion: ignored.data ?? null,
    available: isUpdateAvailable(update?.version, installed.data, ignored.data),
    isChecking: latest.isFetching,
    checkFailed: latest.isError,
    lastCheckedAt: latest.dataUpdatedAt || null,
    checkNow: () => latest.refetch(),
    ignore: (version: string) => ignore.mutate(version),
    unignore: () => ignore.mutate(null),
  };
}
