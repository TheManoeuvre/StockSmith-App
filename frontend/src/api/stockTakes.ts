import { api } from "./client";
import type { DueForCountItem, StockCountSettings } from "./types";

export const stockTakesApi = {
  /** Everything whose cadence says it wants counting, most overdue first. On a database
   * that has never had a stock take this is every active item, which is the honest
   * starting state rather than a fault. */
  overdue: () => api.get<DueForCountItem[]>("/stock-takes/overdue"),
};

export const stockCountSettingsApi = {
  get: () => api.get<StockCountSettings>("/settings/stock-count-settings"),
  update: (input: StockCountSettings) =>
    api.put<StockCountSettings>("/settings/stock-count-settings", input),
};
