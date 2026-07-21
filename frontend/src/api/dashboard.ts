import { api } from "./client";
import type { BuildableProduct, DashboardSummary } from "./types";

export const dashboardApi = {
  summary: () => api.get<DashboardSummary>("/dashboard/summary"),
  maxBuildable: () => api.get<BuildableProduct[]>("/dashboard/max-buildable"),
};
