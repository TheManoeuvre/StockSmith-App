import { api } from "./client";

export type WorkStageKind = "custom" | "make" | "ship";
export type WorkQtyBasis = "unit" | "bom_category";

export interface WorkSettings {
  work_flow_enabled: boolean;
  work_flow_enabled_at: string | null;
}

export interface WorkStage {
  id: number;
  name: string;
  station: string | null;
  kind: WorkStageKind;
  /** Work for one buyer ("make the file"): never pooled, never done ahead. */
  per_order: boolean;
  wait_minutes: number | null;
  typical_minutes: number | null;
  created_at: string;
  route_count: number;
  event_count: number;
}

export interface WorkRouteStep {
  id: number;
  stage_id: number;
  position: number;
  mix_variants: boolean;
  qty_basis: WorkQtyBasis;
  qty_category_id: number | null;
  stage_name: string;
  stage_kind: WorkStageKind;
  station: string | null;
  per_order: boolean;
}

export interface WorkRoute {
  id: number;
  name: string;
  is_default: boolean;
  created_at: string;
  steps: WorkRouteStep[];
  product_count: number;
  category_count: number;
}

export interface WorkRouteStepInput {
  stage_id: number;
  mix_variants: boolean;
  qty_basis: WorkQtyBasis;
  qty_category_id: number | null;
}

export interface WorkVariantPart {
  variant_id: number | null;
  variant_name: string | null;
  qty: number;
  finished_units: number;
}

export interface WorkCard {
  key: string;
  stage_id: number;
  stage_name: string;
  stage_kind: WorkStageKind;
  station: string | null;
  product_id: number;
  product_name: string;
  parts: WorkVariantPart[];
  /** In the stage's own units — blanks for a count-by-category stage, finished items otherwise. */
  qty: number;
  unit_label: string | null;
  due: string | null;
  late: boolean;
  order_ids: number[];
  order_line_id: number | null;
  /** Progress can be recorded with the Done control. Make is a build; Ship happens on the order. */
  can_record: boolean;
  batch_note: string | null;
}

export interface WorkShipLine {
  product_id: number;
  product_name: string;
  variant_name: string | null;
  qty: number;
  ready: boolean;
}

export interface WorkOrderCard {
  order_id: number;
  platform: string | null;
  buyer_name: string | null;
  due: string | null;
  late: boolean;
  lines: WorkShipLine[];
  ready_lines: number;
  total_lines: number;
  ready: boolean;
  waiting_for: string[];
}

export interface WorkStation {
  name: string | null;
  count: number;
  earliest_due: string | null;
}

export interface WorkList {
  enabled: boolean;
  generated_at: string;
  stations: WorkStation[];
  /** The station Ship belongs to — where "ready to ship" and "waiting" sit when filtering. */
  ship_station: string | null;
  cards: WorkCard[];
  ship_cards: WorkOrderCard[];
  waiting_orders: WorkOrderCard[];
}

export interface WorkSummary {
  enabled: boolean;
  late: number;
  due_today: number;
  open_cards: number;
  ready_to_ship: number;
}

export interface WorkProgressInput {
  product_id: number;
  variant_id?: number | null;
  stage_id: number;
  qty: number;
  order_line_id?: number | null;
  note?: string | null;
}

export interface WorkEvent {
  id: number;
  product_id: number;
  variant_id: number | null;
  order_line_id: number | null;
  stage_id: number;
  qty: number;
  note: string | null;
  created_at: string;
  stage_name: string | null;
  product_name: string | null;
  variant_name: string | null;
}

export type WorkStageInput = {
  name: string;
  station?: string | null;
  per_order?: boolean;
  wait_minutes?: number | null;
  typical_minutes?: number | null;
};

export const workApi = {
  settings: () => api.get<WorkSettings>("/work/settings"),
  setEnabled: (work_flow_enabled: boolean) => api.patch<WorkSettings>("/work/settings", { work_flow_enabled }),

  stages: () => api.get<WorkStage[]>("/work/stages"),
  createStage: (input: WorkStageInput) => api.post<WorkStage>("/work/stages", input),
  updateStage: (id: number, input: Partial<WorkStageInput>) => api.patch<WorkStage>(`/work/stages/${id}`, input),
  deleteStage: (id: number) => api.delete<void>(`/work/stages/${id}`),

  routes: () => api.get<WorkRoute[]>("/work/routes"),
  createRoute: (name: string, steps: WorkRouteStepInput[]) => api.post<WorkRoute>("/work/routes", { name, steps }),
  updateRoute: (id: number, input: { name?: string; steps?: WorkRouteStepInput[] }) =>
    api.patch<WorkRoute>(`/work/routes/${id}`, input),
  makeDefault: (id: number) => api.post<WorkRoute>(`/work/routes/${id}/make-default`, {}),
  deleteRoute: (id: number) => api.delete<void>(`/work/routes/${id}`),

  list: () => api.get<WorkList>("/work/list"),
  summary: () => api.get<WorkSummary>("/work/summary"),
  recordProgress: (input: WorkProgressInput) => api.post<WorkEvent[]>("/work/progress", input),
  undo: (eventId: number) => api.post<WorkEvent>(`/work/progress/${eventId}/undo`, {}),
  events: (limit = 20) => api.get<WorkEvent[]>(`/work/events?limit=${limit}`),
};
