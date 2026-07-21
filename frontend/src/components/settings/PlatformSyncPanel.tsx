import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { platformsApi, type BulkListingSyncResult, type SyncCommitResult, type SyncPreviewResult } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";

const SYNC_LOG_PAGE_SIZE = 10;

export function PlatformSyncPanel({ platform }: { platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<SyncPreviewResult | null>(null);
  const [commitResult, setCommitResult] = useState<SyncCommitResult | null>(null);
  const [bulkSyncResult, setBulkSyncResult] = useState<BulkListingSyncResult | null>(null);
  const [syncStartDate, setSyncStartDate] = useState("");
  const [logPage, setLogPage] = useState(0);

  const { data: logData } = useQuery({
    queryKey: ["platforms", platform, "sync-log", logPage],
    queryFn: () => platformsApi.syncLog(platform, SYNC_LOG_PAGE_SIZE, logPage * SYNC_LOG_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const log = logData?.items;
  const logTotal = logData?.total ?? 0;
  const { data: platformStatus } = useQuery({
    queryKey: ["platforms", platform, "status"],
    queryFn: () => platformsApi.status(platform),
  });

  useEffect(() => {
    if (platformStatus?.sync_start_date) setSyncStartDate(platformStatus.sync_start_date);
  }, [platformStatus?.sync_start_date]);

  const saveSyncStartDateMutation = useMutation({
    mutationFn: () => platformsApi.updateSyncStartDate(platform, syncStartDate),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platforms", platform, "status"] }),
  });

  const previewMutation = useMutation({
    mutationFn: () => platformsApi.previewSync(platform),
    onSuccess: (result) => {
      setPreview(result);
      setCommitResult(null);
      setLogPage(0);
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "sync-log"] });
    },
  });

  const commitMutation = useMutation({
    mutationFn: () => platformsApi.syncOrders(platform),
    onSuccess: (result) => {
      setCommitResult(result);
      setLogPage(0);
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "sync-log"] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  const checkAllMutation = useMutation({
    mutationFn: () => platformsApi.checkAllListings(platform),
    onSuccess: (result) => {
      setBulkSyncResult(result);
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "all-sync-status"] });
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products"] });
    },
  });

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">Order sync</p>
          <p className="text-sm text-slate-500">
            Manual only — no automatic polling is set up yet. Always preview before importing.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => previewMutation.mutate()}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm"
          >
            {previewMutation.isPending ? "Fetching…" : "Preview sync"}
          </button>
          <button
            onClick={() => commitMutation.mutate()}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
          >
            {commitMutation.isPending ? "Importing…" : "Sync now"}
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">Sync start date</span>
          <input
            type="date"
            className="rounded border border-slate-300 px-2 py-1"
            value={syncStartDate}
            onChange={(e) => setSyncStartDate(e.target.value)}
          />
        </label>
        <button
          onClick={() => saveSyncStartDateMutation.mutate()}
          disabled={
            !syncStartDate || syncStartDate === platformStatus?.sync_start_date || saveSyncStartDateMutation.isPending
          }
          className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
        >
          {saveSyncStartDateMutation.isPending ? "Saving…" : "Save"}
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Orders placed before this date are never imported, regardless of sync history. Once automatic sync lands,
        it'll only pull orders placed after this date.
      </p>
      <ErrorBanner error={saveSyncStartDateMutation.error} />

      <ErrorBanner error={previewMutation.error ?? commitMutation.error} />

      <div className="flex items-center justify-between border-t border-slate-200 pt-3">
        <div>
          <p className="font-medium">Product SKU sync check</p>
          <p className="text-sm text-slate-500">
            Tests every active product/variant's SKU against {label}'s live listing catalog.
          </p>
        </div>
        <button
          onClick={() => checkAllMutation.mutate()}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm"
        >
          {checkAllMutation.isPending ? "Checking…" : "Check all products"}
        </button>
      </div>
      <ErrorBanner error={checkAllMutation.error} />
      {bulkSyncResult && (
        <div className="rounded bg-slate-50 p-2 text-sm">
          <strong>{bulkSyncResult.synced_count}</strong> synced, <strong>{bulkSyncResult.partial_count}</strong>{" "}
          partial, <strong>{bulkSyncResult.not_found_count}</strong> not found (
          {bulkSyncResult.summaries.length} product(s) checked).
        </div>
      )}

      {preview && <PreviewResultView result={preview} label={label} />}
      {commitResult && (
        <div className="rounded bg-green-50 p-2 text-sm text-green-800">
          Imported {commitResult.created_count} new order(s), updated {commitResult.updated_count} existing,{" "}
          {commitResult.shipped_count} marked shipped, {commitResult.needs_mapping_count} line(s) need SKU mapping.
        </div>
      )}

      {log && log.length > 0 && (
        <div>
          <p className="mb-1 text-sm font-medium text-slate-600">Recent sync activity</p>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse bg-white text-left text-xs shadow-sm">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="p-1.5">When</th>
                  <th className="p-1.5">Mode</th>
                  <th className="p-1.5">Status</th>
                  <th className="p-1.5">Fetched</th>
                  <th className="p-1.5">New</th>
                  <th className="p-1.5">Shipped</th>
                  <th className="p-1.5">Needs mapping</th>
                  <th className="p-1.5">Error</th>
                </tr>
              </thead>
              <tbody>
                {log.map((run) => (
                  <tr key={run.id} className="border-b border-slate-100">
                    <td className="p-1.5">{new Date(run.started_at).toLocaleString()}</td>
                    <td className="p-1.5">{run.mode}</td>
                    <td className="p-1.5">
                      <span className={run.status === "success" ? "text-green-700" : "text-red-600"}>
                        {run.status}
                      </span>
                    </td>
                    <td className="p-1.5">{run.fetched_count}</td>
                    <td className="p-1.5">{run.new_count}</td>
                    <td className="p-1.5">{run.shipped_count}</td>
                    <td className="p-1.5">{run.needs_mapping_count}</td>
                    <td className="p-1.5 text-red-600">{run.error_message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-1.5 flex items-center justify-between text-xs text-slate-500">
            <span>
              Showing {logPage * SYNC_LOG_PAGE_SIZE + 1}–{Math.min(logPage * SYNC_LOG_PAGE_SIZE + log.length, logTotal)}{" "}
              of {logTotal}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setLogPage((p) => Math.max(0, p - 1))}
                disabled={logPage === 0}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
              >
                Prev
              </button>
              <button
                onClick={() => setLogPage((p) => p + 1)}
                disabled={(logPage + 1) * SYNC_LOG_PAGE_SIZE >= logTotal}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PreviewResultView({ result, label }: { result: SyncPreviewResult; label: string }) {
  return (
    <div className="rounded bg-slate-50 p-2 text-sm">
      <p className="mb-2">
        Fetched <strong>{result.fetched_count}</strong>, <strong>{result.new_count}</strong> new,{" "}
        <strong>{result.needs_mapping_count}</strong> line(s) would need SKU mapping. Nothing has been imported yet.
      </p>
      <div className="flex flex-col gap-2">
        {result.orders.map((order) => (
          <details key={order.external_order_id} className="rounded border border-slate-200 bg-white p-2">
            <summary className="cursor-pointer">
              {order.buyer_name ?? "—"} — receipt {order.external_order_id}
              {order.already_imported && <span className="ml-2 text-xs text-slate-400">(already imported)</span>}
              {order.is_cancelled && <span className="ml-2 text-xs text-red-600">cancelled</span>}
              {order.is_shipped && <span className="ml-2 text-xs text-green-700">shipped</span>}
            </summary>
            <table className="mt-2 w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="p-1">SKU</th>
                  <th className="p-1">Qty</th>
                  <th className="p-1">Match</th>
                </tr>
              </thead>
              <tbody>
                {order.lines.map((line) => (
                  <tr key={line.external_line_id} className="border-b border-slate-100">
                    <td className="p-1">{line.sku ?? "—"}</td>
                    <td className="p-1">{line.qty}</td>
                    <td className="p-1">
                      {line.matched_product_id ? (
                        <span>
                          {line.matched_product_name}
                          {line.matched_variant_name ? ` — ${line.matched_variant_name}` : ""}
                        </span>
                      ) : (
                        <span className="text-amber-700">No match — needs mapping</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-400">Raw {label} response</summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-900 p-2 text-xs text-slate-100">
                {JSON.stringify(order.raw, null, 2)}
              </pre>
            </details>
          </details>
        ))}
      </div>
    </div>
  );
}
