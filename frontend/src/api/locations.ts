import { api, downloadCsv, uploadCsv, type CsvImportResult } from "./client";

/** What a placement belongs to: a material, or a product with an optional variant. */
export interface ItemRef {
  material_id?: number | null;
  product_id?: number | null;
  variant_id?: number | null;
}

export interface LocationRow {
  id: number;
  parent_id: number | null;
  name: string;
  code: string;
  notes: string | null;
  archived: boolean;
  /** Items placed directly in this location. */
  own_items: number;
  /** Items placed here or anywhere beneath it. */
  rolled_up_items: number;
  /** Something in or under this location has placed more than exists. */
  needs_attention: boolean;
}

export interface LocationTree {
  locations: LocationRow[];
  total_locations: number;
  attention_locations: number;
  empty_locations: number;
  unplaced_items: number;
  placeable_items: number;
}

export interface RangePreview {
  names: { name: string; exists: boolean }[];
  new_count: number;
  error: string | null;
}

export interface LocationContent {
  placement_id: number;
  material_id: number | null;
  product_id: number | null;
  variant_id: number | null;
  name: string;
  sku: string | null;
  unit: string;
  qty: string;
  is_primary: boolean;
  /** How much more is placed than exists, for this item overall; "0" when healthy. */
  drift: string;
}

export type PlacementEventKind = "place" | "move" | "remove" | "auto_deduct" | "auto_add" | "count";

export interface LocationHistoryEntry {
  id: number;
  kind: PlacementEventKind;
  item_name: string;
  qty: string;
  from_path: string | null;
  to_path: string | null;
  reason: string | null;
  created_at: string;
}

export interface LocationDetail {
  id: number;
  parent_id: number | null;
  name: string;
  code: string;
  notes: string | null;
  archived: boolean;
  path: { id: number; name: string }[];
  contents: LocationContent[];
  children: LocationRow[];
  history: LocationHistoryEntry[];
  history_count: number;
}

export interface StoredPlacement {
  id: number;
  location_id: number;
  location_path: string[];
  qty: string;
  is_primary: boolean;
}

export interface StoredIn {
  name: string;
  unit: string;
  total: string;
  placed: string;
  unassigned: string;
  /** How much more is placed than exists; "0" when healthy. */
  drift: string;
  /** Exactly one placement: the quantity follows stock changes automatically. */
  follows_stock: boolean;
  /** Set when the primary place is empty but another still holds stock. */
  replenish?: {
    to_location_id: number;
    to_path: string[];
    from_location_id: number;
    from_path: string[];
    qty: string;
  } | null;
  /** False once someone has said not to ask where this item goes. */
  ask_to_put_away?: boolean;
  placements: StoredPlacement[];
}

export interface ItemLocationSummary {
  material_id: number | null;
  product_id: number | null;
  variant_id: number | null;
  name: string;
  sku: string | null;
  unit: string;
  primary_path: string[] | null;
  extra_places: number;
  placed: string;
  total: string;
  drift: string;
}

function itemQuery(ref: ItemRef): string {
  const p = new URLSearchParams();
  if (ref.material_id != null) p.set("material_id", String(ref.material_id));
  if (ref.product_id != null) p.set("product_id", String(ref.product_id));
  if (ref.variant_id != null) p.set("variant_id", String(ref.variant_id));
  return p.toString();
}

export const locationsApi = {
  tree: () => api.get<LocationTree>("/locations"),
  get: (id: number) => api.get<LocationDetail>(`/locations/${id}`),
  create: (body: { name: string; parent_id: number | null; notes?: string | null }) =>
    api.post<LocationRow>("/locations", body),
  update: (id: number, body: { name?: string; notes?: string | null; parent_id?: number | null }) =>
    api.patch<LocationRow>(`/locations/${id}`, body),
  archive: (id: number) => api.post<LocationRow>(`/locations/${id}/archive`),
  unarchive: (id: number) => api.post<LocationRow>(`/locations/${id}/unarchive`),
  remove: (id: number) => api.delete<void>(`/locations/${id}`),
  previewRange: (parent_id: number | null, pattern: string) =>
    api.post<RangePreview>("/locations/range/preview", { parent_id, pattern }),
  generateRange: (parent_id: number | null, pattern: string) =>
    api.post<LocationRow[]>("/locations/range", { parent_id, pattern }),
  exportCsv: () => downloadCsv("/locations/export", "locations.csv"),
  importCsv: (fileBytes: Uint8Array, filename: string): Promise<CsvImportResult> =>
    uploadCsv("/locations/import", fileBytes, filename),
  emptyInto: (id: number, to_location_id: number) =>
    api.post<{ moved: number }>(`/locations/${id}/empty-into`, { to_location_id }),

  setAskToPutAway: (ref: ItemRef, ask: boolean) => api.put<void>("/placements/put-away-preference", { ...ref, ask }),
  storedIn: (ref: ItemRef) => api.get<StoredIn>(`/placements/stored-in?${itemQuery(ref)}`),
  summaries: () => api.get<ItemLocationSummary[]>("/placements/summary"),
  place: (ref: ItemRef, location_id: number, qty: string) =>
    api.post<StoredPlacement>("/placements", { ...ref, location_id, qty }),
  moveItem: (ref: ItemRef, from_location_id: number, to_location_id: number, qty: string) =>
    api.post<void>("/placements/move", { ...ref, from_location_id, to_location_id, qty }),
  makePrimary: (placementId: number) => api.post<void>(`/placements/${placementId}/primary`),
  reduce: (placementId: number, qty: string) => api.post<void>(`/placements/${placementId}/reduce`, { qty }),
  unplace: (placementId: number) => api.delete<void>(`/placements/${placementId}`),
};

/** Query keys touched by anything that places or moves stock, so every view refreshes together. */
export const LOCATION_QUERY_KEYS = [["locations"], ["location"], ["stored-in"], ["location-summaries"]] as const;
