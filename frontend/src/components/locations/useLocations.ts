import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { LOCATION_QUERY_KEYS, locationsApi } from "../../api/locations";

/** The whole location tree with counts. One query, shared by the page, the pickers and the sidebar. */
export function useLocationTree() {
  return useQuery({ queryKey: ["locations"], queryFn: locationsApi.tree });
}

/** Anything that places, moves or creates refreshes every view of locations together. */
export function invalidateLocations(queryClient: QueryClient) {
  for (const key of LOCATION_QUERY_KEYS) void queryClient.invalidateQueries({ queryKey: [...key] });
}

export function useInvalidateLocations() {
  const queryClient = useQueryClient();
  return () => invalidateLocations(queryClient);
}
