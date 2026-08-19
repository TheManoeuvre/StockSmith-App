import { api, baseUrl, authHeaders, platformFetch, downloadCsv } from "./client";
import type {
  ApproveResult,
  DueForCountItem,
  ScopePreview,
  StockCountSettings,
  StockTake,
  StockTakeCreated,
  StockTakeDetail,
  StockTakeImportResult,
  StockTakeScope,
  UnresolvedVariance,
} from "./types";

export const stockTakesApi = {
  list: () => api.get<StockTake[]>("/stock-takes"),
  get: (id: number) => api.get<StockTakeDetail>(`/stock-takes/${id}`),

  /** Everything whose cadence says it wants counting, most overdue first. On a database
   * that has never had a stock take this is every active item, which is the honest
   * starting state rather than a fault. */
  overdue: () => api.get<DueForCountItem[]>("/stock-takes/overdue"),

  /** Flagged lines from takes that have since closed. Separate from any one take, so
   * following one up doesn't depend on remembering where it came from. */
  unresolvedVariances: () => api.get<UnresolvedVariance[]>("/stock-takes/unresolved-variances"),

  /** What a take with this scope would contain. Writes nothing. */
  previewScope: (scope: StockTakeScope) => api.post<ScopePreview>("/stock-takes/preview-scope", scope),

  create: (scope: StockTakeScope) => api.post<StockTakeCreated>("/stock-takes", scope),
  remove: (id: number) => api.delete<void>(`/stock-takes/${id}`),

  setLineCount: (id: number, lineId: number, countedQty: string | null, notes: string | null) =>
    api.patch<StockTakeDetail>(`/stock-takes/${id}/lines/${lineId}`, {
      counted_qty: countedQty,
      notes,
    }),

  setLineCounts: (id: number, lines: { line_id: number; counted_qty: string | null; notes: string | null }[]) =>
    api.put<StockTakeDetail>(`/stock-takes/${id}/lines`, { lines }),

  resolveLine: (id: number, lineId: number, action: "accept_counted" | "accept_system" | "reset") =>
    api.post<StockTakeDetail>(`/stock-takes/${id}/lines/${lineId}/resolve`, { action }),

  approve: (id: number) => api.post<ApproveResult>(`/stock-takes/${id}/approve`, {}),

  exportCsv: (id: number) => downloadCsv(`/stock-takes/${id}/export`, `stock-take-${id}.csv`),

  /** Called twice with the same file: dry_run first for the confirmation screen, then
   * again to apply. Stateless on the server, so the bytes go up twice — which is free
   * next to the alternative of trusting the client about what was approved. */
  importCsv: async (
    id: number,
    fileBytes: Uint8Array,
    filename: string,
    { dryRun, onError }: { dryRun: boolean; onError: "skip" | "fail" },
  ): Promise<StockTakeImportResult> => {
    const url = `${await baseUrl()}/api/v1/stock-takes/${id}/import?dry_run=${dryRun}&on_error=${onError}`;
    const headers = await authHeaders();
    const formData = new FormData();
    formData.append("file", new Blob([fileBytes as BlobPart], { type: "text/csv" }), filename);
    const response = await platformFetch(url, { method: "POST", headers, body: formData });
    if (!response.ok) {
      let detail = `Import failed (${response.status})`;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* a non-JSON body just leaves the status-code message */
      }
      throw new Error(detail);
    }
    return (await response.json()) as StockTakeImportResult;
  },
};

export const stockCountSettingsApi = {
  get: () => api.get<StockCountSettings>("/settings/stock-count-settings"),
  update: (input: StockCountSettings) =>
    api.put<StockCountSettings>("/settings/stock-count-settings", input),
};
