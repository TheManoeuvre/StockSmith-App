export const GITHUB_REPO_URL = "https://github.com/TheManoeuvre/StockSmith-App";
export const GITHUB_RELEASES_URL = `${GITHUB_REPO_URL}/releases`;

export function releaseUrl(version: string): string {
  return `${GITHUB_RELEASES_URL}/tag/v${version}`;
}

/** Orders two x.y.z versions numerically. Anything unparseable compares as 0.0.0, which errs
 * towards "not newer" — a malformed feed value should never raise a badge. */
export function compareVersions(a: string, b: string): number {
  const parse = (v: string) => {
    const parts = v.trim().replace(/^v/, "").split("-")[0].split(".").map((n) => Number.parseInt(n, 10));
    return [0, 1, 2].map((i) => (Number.isFinite(parts[i]) ? parts[i] : 0));
  };
  const [pa, pb] = [parse(a), parse(b)];
  for (let i = 0; i < 3; i++) {
    if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  }
  return 0;
}

/**
 * Whether the offered release should be shown as available.
 *
 * Ignoring is remembered as the one version the user dismissed, so a newer release compares
 * unequal and notifications resume without any extra bookkeeping. Compared as "ignored is at
 * least as new", not just equal, so a release that gets pulled and replaced by an older one
 * can't resurface a version the user already dismissed.
 */
export function isUpdateAvailable(
  latest: string | null | undefined,
  installed: string | null | undefined,
  ignored: string | null | undefined
): boolean {
  if (!latest) return false;
  if (installed && compareVersions(latest, installed) <= 0) return false;
  if (ignored && compareVersions(latest, ignored) <= 0) return false;
  return true;
}
