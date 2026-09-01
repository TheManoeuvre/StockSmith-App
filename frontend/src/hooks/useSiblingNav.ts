import { useQueryClient } from "@tanstack/react-query";

/**
 * Prev/Next for a detail panel, computed from whatever's already sitting in the React
 * Query cache for the list behind it — no extra fetch, since the list route is always
 * mounted underneath the panel.
 *
 * `queryKeyPrefix` matches loosely (`getQueriesData` prefix-matches), so this picks up
 * whichever variant of the list query (a given page/filter combination) is currently
 * cached — normally exactly one, since that's whatever the list route the user came from
 * is actually showing. `extractItems` unwraps each cached query's payload into the ordered
 * row array that query represents (a plain array for an unpaginated list, `.items` for a
 * paginated one).
 */
export function useSiblingNav<T extends { id: number }>(
  queryKeyPrefix: readonly unknown[],
  currentId: number,
  extractItems: (data: unknown) => T[] | undefined,
): { prevId: number | null; nextId: number | null } {
  const queryClient = useQueryClient();
  const cached = queryClient.getQueriesData({ queryKey: queryKeyPrefix });

  // Newest-fetched cache entry wins when more than one is around (e.g. a filter changed
  // and the old page is still cached) — that's whichever list view is actually on screen.
  //
  // `queryKeyPrefix` matching is loose (["orders"] also matches ["orders", 7], the single-
  // order query), so `extractItems` can be handed a payload that was never a list at all —
  // Array.isArray is what actually rules that out; a truthy-but-wrong-shape cast from
  // `extractItems` would otherwise reach `.findIndex` below and throw.
  let items: T[] | undefined;
  let newestUpdatedAt = -1;
  for (const [key, data] of cached) {
    const extracted = extractItems(data);
    if (!Array.isArray(extracted)) continue;
    const state = queryClient.getQueryState(key);
    const updatedAt = state?.dataUpdatedAt ?? 0;
    if (updatedAt >= newestUpdatedAt) {
      newestUpdatedAt = updatedAt;
      items = extracted;
    }
  }
  if (!items) return { prevId: null, nextId: null };

  const index = items.findIndex((item) => item.id === currentId);
  if (index === -1) return { prevId: null, nextId: null };

  return {
    prevId: index > 0 ? items[index - 1].id : null,
    nextId: index < items.length - 1 ? items[index + 1].id : null,
  };
}
