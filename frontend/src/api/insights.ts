import { api } from "./client";

export type InsightsGranularity = "day" | "week" | "month";
/** Every value the backend's `channels` filter accepts. Shopify is valid there but has no
 *  adapter yet, so the UI never offers it (see lib/platforms CONNECTABLE_PLATFORMS). */
export type InsightsChannel = "etsy" | "ebay" | "shopify" | "square" | "manual";
export type InsightsView = "products" | "variants" | "categories";
export type InsightsSortKey =
  | "name"
  | "units"
  | "revenue"
  | "cogs"
  | "net_profit"
  | "margin_percent"
  | "profit_per_unit"
  | "fail_rate_percent"
  | "share_of_profit_percent";

// Money and percentages arrive as strings (Pydantic serialises Decimal that way).

export interface InsightsTotals {
  orders: number;
  units: number;
  revenue: string;
  platform_fees: string;
  postage_cost: string;
  materials_cogs: string;
  kitting_cogs: string;
  replacement_cost: string;
  refunded: string;
  failed_build_cost: string;
  net_profit: string;
  margin_percent: string | null;
  average_order_value: string | null;
  average_profit_per_order: string | null;
  refund_rate_percent: string | null;
  profit_unavailable_orders: number;
}

export interface InsightsBucket {
  period_start: string;
  orders: number;
  units: number;
  revenue: string;
  net_profit: string;
  margin_percent: string | null;
}

export interface InsightsChannelRow {
  channel: InsightsChannel;
  orders: number;
  units: number;
  revenue: string;
  platform_fees: string;
  net_profit: string;
  margin_percent: string | null;
  average_order_value: string | null;
}

export interface InsightsOverview {
  start: string;
  end: string;
  granularity: InsightsGranularity;
  include_failed_builds: boolean;
  totals: InsightsTotals;
  trend: InsightsBucket[];
  channels: InsightsChannelRow[];
  compare_start: string | null;
  compare_end: string | null;
  compare_totals: InsightsTotals | null;
  compare_trend: InsightsBucket[] | null;
}

export interface InsightsProductRow {
  key: string;
  name: string;
  sub: string;
  product_id: number | null;
  variant_id: number | null;
  units: number;
  revenue: string;
  cogs: string;
  failed_build_cost: string;
  net_profit: string;
  net_profit_before_failed_builds: string;
  margin_percent: string | null;
  margin_before_failed_builds_percent: string | null;
  profit_per_unit: string | null;
  share_of_profit_percent: string | null;
  fail_rate_percent: string | null;
  channel_units: Record<string, number>;
  trend: string[];
}

export interface InsightsProducts {
  view: InsightsView;
  rows: InsightsProductRow[];
  total_rows: number;
  page: number;
  page_size: number;
  unmapped_units: number;
  profit_unavailable_units: number;
  totals: InsightsTotals;
}

export interface InsightsCostComponent {
  key: string;
  label: string;
  amount: string;
}

export interface InsightsChannelCosts {
  channel: InsightsChannel;
  revenue: string;
  fees: string;
  postage: string;
  materials: string;
  packaging: string;
  resends: string;
  failed_builds: string;
  net_profit: string;
}

export interface InsightsAmountRow {
  key: string;
  label: string;
  amount: string;
}

export interface InsightsCosts {
  include_failed_builds: boolean;
  waterfall: InsightsCostComponent[];
  channels: InsightsChannelCosts[];
  materials_by_category: InsightsAmountRow[];
  resends_by_reason: InsightsAmountRow[];
  failed_builds: InsightsProductRow[];
  failed_builds_total: string;
  failed_builds_percent_of_materials: string | null;
}

export interface InsightsFlaggedOrder {
  order_id: number;
  external_order_id: string | null;
  channel: InsightsChannel;
  placed_on: string;
  issues: string[];
}

export interface InsightsQuality {
  cogs_pending: number;
  postage_missing: number;
  financials_not_synced: number;
  unmapped_lines: number;
  flagged_total: number;
  flagged: InsightsFlaggedOrder[];
}

/** What every Insights request shares. `channels: null` means every channel. */
export interface InsightsScope {
  start: string;
  end: string;
  channels: InsightsChannel[] | null;
  includeFailedBuilds: boolean;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  return `?${search.toString()}`;
}

function scopeParams(scope: InsightsScope) {
  return {
    start: scope.start,
    end: scope.end,
    channels: scope.channels?.join(",") ?? null,
    include_failed_builds: scope.includeFailedBuilds,
  };
}

export interface ProductsRequest {
  view: InsightsView;
  q: string;
  sort: InsightsSortKey;
  descending: boolean;
  page: number;
  pageSize: number;
  granularity: InsightsGranularity;
  productId?: number;
}

export const insightsApi = {
  overview: (
    scope: InsightsScope,
    granularity: InsightsGranularity,
    compare: { start: string; end: string } | null,
  ) =>
    api.get<InsightsOverview>(
      `/insights/overview${query({
        ...scopeParams(scope),
        granularity,
        compare_start: compare?.start,
        compare_end: compare?.end,
      })}`,
    ),
  products: (scope: InsightsScope, request: ProductsRequest) =>
    api.get<InsightsProducts>(
      `/insights/products${query({
        ...scopeParams(scope),
        view: request.view,
        q: request.q,
        sort: request.sort,
        descending: request.descending,
        page: request.page,
        page_size: request.pageSize,
        granularity: request.granularity,
        product_id: request.productId,
      })}`,
    ),
  costs: (scope: InsightsScope) => api.get<InsightsCosts>(`/insights/costs${query(scopeParams(scope))}`),
  quality: (scope: InsightsScope) =>
    api.get<InsightsQuality>(
      `/insights/quality${query({
        start: scope.start,
        end: scope.end,
        channels: scope.channels?.join(",") ?? null,
      })}`,
    ),
};
