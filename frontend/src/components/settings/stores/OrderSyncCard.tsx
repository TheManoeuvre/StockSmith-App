import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  platformsApi,
  type PlatformStatus,
  type SyncCommitResult,
  type SyncPreviewResult,
} from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { PLATFORM_LABELS } from "../../../lib/platforms";
import { useEditableCopy } from "../../../hooks/useEditableCopy";
import { useSaveStatus } from "../../../hooks/useSaveStatus";
import { ErrorBanner } from "../../common/ErrorBanner";
import { FieldRow } from "../../common/FieldRow";
import { SaveButton } from "../../common/SaveButton";
import { Switch } from "../../common/Switch";
import { SettingsCard } from "../SettingsCard";

const SYNC_LOG_PAGE_SIZE = 10;

interface OrderSyncForm {
  autoSync: boolean;
  intervalMinutes: string;
  startDate: string;
}

/**
 * Inbound order sync for one store: how it runs, when it last ran, and the log of every run.
 *
 * The three settings are one buffered form with one Save. They used to be three different
 * interactions in one panel — a checkbox that saved instantly, an interval that saved on
 * blur, and a start date with its own bare Save — while every other Settings form went
 * through useEditableCopy and SaveButton. Start date and the other two still hit two
 * endpoints; that's the mutation's problem, not the user's.
 */
export function OrderSyncCard({
  platform,
  status,
}: {
  platform: ListingPlatform;
  status: PlatformStatus | undefined;
}) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<SyncPreviewResult | null>(null);
  const [commitResult, setCommitResult] = useState<SyncCommitResult | null>(null);
  const [logPage, setLogPage] = useState(0);

  const seed = useMemo<OrderSyncForm | undefined>(
    () =>
      status
        ? {
            autoSync: status.auto_sync_enabled,
            intervalMinutes: String(status.sync_interval_minutes),
            startDate: status.sync_start_date ?? "",
          }
        : undefined,
    [status],
  );
  const {
    value: form,
    setValue: setForm,
    isDirty,
    markSaved,
  } = useEditableCopy<OrderSyncForm>({
    key: "order-sync",
    label: `${label} order sync`,
    initial: { autoSync: false, intervalMinutes: "15", startDate: "" },
    seed,
    seedKey: platform,
  });
  const setField = <K extends keyof OrderSyncForm>(field: K, next: OrderSyncForm[K]) =>
    setForm((prev) => ({ ...prev, [field]: next }));

  const invalidateStatus = () => {
    queryClient.invalidateQueries({
      queryKey: ["platforms", platform, "status"],
    });
    queryClient.invalidateQueries({ queryKey: ["platforms", "sync-summary"] });
  };

  const intervalMinutes = parseInt(form.intervalMinutes, 10);
  const intervalValid = Number.isInteger(intervalMinutes) && intervalMinutes >= 1;

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!status) return;
      const settingsChanged =
        form.autoSync !== status.auto_sync_enabled || intervalMinutes !== status.sync_interval_minutes;
      if (settingsChanged) {
        await platformsApi.updateSyncSettings(platform, {
          auto_sync_enabled: form.autoSync,
          sync_interval_minutes: intervalMinutes,
        });
      }
      if (form.startDate && form.startDate !== (status.sync_start_date ?? "")) {
        await platformsApi.updateSyncStartDate(platform, form.startDate);
      }
    },
    onSuccess: () => {
      markSaved({ ...form, intervalMinutes: String(intervalMinutes) });
      invalidateStatus();
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  const { data: logData } = useQuery({
    queryKey: ["platforms", platform, "sync-log", logPage],
    queryFn: () => platformsApi.syncLog(platform, SYNC_LOG_PAGE_SIZE, logPage * SYNC_LOG_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const log = logData?.items;
  const logTotal = logData?.total ?? 0;

  const afterRun = () => {
    setLogPage(0);
    queryClient.invalidateQueries({
      queryKey: ["platforms", platform, "sync-log"],
    });
    invalidateStatus();
  };
  const previewMutation = useMutation({
    mutationFn: () => platformsApi.previewSync(platform),
    onSuccess: (result) => {
      setPreview(result);
      setCommitResult(null);
      afterRun();
    },
  });
  const commitMutation = useMutation({
    mutationFn: () => platformsApi.syncOrders(platform),
    onSuccess: (result) => {
      setCommitResult(result);
      afterRun();
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["order-counts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  return (
    <SettingsCard
      title="Order sync"
      help={`New ${label} orders are imported here. Nothing is written back except stock quantities.`}
      action={
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending}
            className="h-7 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium disabled:opacity-50"
          >
            {previewMutation.isPending ? "Fetching…" : "Preview"}
          </button>
          <button
            type="button"
            onClick={() => commitMutation.mutate()}
            disabled={commitMutation.isPending}
            className="h-7 rounded-md bg-slate-900 px-2.5 text-xs font-semibold text-white disabled:opacity-50"
          >
            {commitMutation.isPending ? "Importing…" : "Sync now"}
          </button>
        </div>
      }
    >
      {status?.last_sync_attempt_at && (
        <p className="text-xs text-slate-500">
          Last run {new Date(status.last_sync_attempt_at).toLocaleString()}
          {status.last_sync_status === "running" ? (
            <span className="text-amber-700"> — still running</span>
          ) : status.last_sync_status === "success" ? (
            <span className="text-green-700"> — succeeded</span>
          ) : (
            <span className="text-red-600">
              {" "}
              — failed
              {status.last_sync_error ? `: ${status.last_sync_error}` : ""}
            </span>
          )}
          {status.last_sync_success_at && status.last_sync_success_at !== status.last_sync_attempt_at && (
            <> (last success {new Date(status.last_sync_success_at).toLocaleString()})</>
          )}
        </p>
      )}
      {/* The hold widens every fetch until the unpaid order resolves, so it shouldn't be
          invisible state — a hold stuck on an order that will never settle needs a symptom
          the user can see. */}
      {status?.unpaid_hold_since && (
        <p className="rounded bg-amber-50 p-2 text-xs text-amber-800">
          Watching for payment on one or more orders — the sync window is held open back to{" "}
          {new Date(status.unpaid_hold_since).toLocaleString()}. They'll import automatically once {label}{" "}
          confirms payment, and reserve no stock until then.
        </p>
      )}

      <div className="flex flex-col gap-2">
        <FieldRow label="Automatic sync">
          <div className="flex items-center gap-2 text-sm">
            <Switch
              id={`${platform}-auto-sync`}
              checked={form.autoSync}
              onChange={(checked) => setField("autoSync", checked)}
              ariaLabel="Automatic sync"
            />
            <span className="text-slate-500">every</span>
            <input
              type="number"
              min={1}
              aria-label="Sync interval in minutes"
              className="w-16 rounded border border-slate-300 px-2 py-1 text-right tabular-nums"
              value={form.intervalMinutes}
              onChange={(e) => setField("intervalMinutes", e.target.value)}
            />
            <span className="text-slate-500">min</span>
          </div>
        </FieldRow>
        <FieldRow label="Import orders from">
          <input
            type="date"
            aria-label="Import orders from"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={form.startDate}
            onChange={(e) => setField("startDate", e.target.value)}
          />
        </FieldRow>
        <p className="text-xs text-slate-500">
          Orders placed before this date are never imported, by hand or automatically.
        </p>
        <div>
          <SaveButton
            isDirty={isDirty}
            isPending={saveMutation.isPending}
            status={saveStatus}
            enabledWhen={isDirty && intervalValid}
            onClick={() => saveMutation.mutate()}
          >
            Save
          </SaveButton>
        </div>
        <ErrorBanner error={saveMutation.error} />
      </div>

      <ErrorBanner error={previewMutation.error ?? commitMutation.error} />
      {preview && <PreviewResultView result={preview} label={label} />}
      {commitResult && (
        <div className="rounded bg-green-50 p-2 text-sm text-green-800">
          Imported {commitResult.created_count} new {commitResult.created_count === 1 ? "order" : "orders"},
          updated {commitResult.updated_count} existing, {commitResult.shipped_count} marked shipped,{" "}
          {commitResult.needs_mapping_count}{" "}
          {commitResult.needs_mapping_count === 1 ? "line needs" : "lines need"} SKU mapping.
          {commitResult.skipped_unpaid_count > 0 && (
            <>
              {" "}
              <strong>
                {commitResult.skipped_unpaid_count} not imported — payment hasn't settled yet.
              </strong>{" "}
              They'll be imported automatically once it does.
            </>
          )}
        </div>
      )}

      {log && log.length > 0 && (
        <div className="border-t border-slate-100 pt-3">
          <h3 className="text-sm font-medium">Recent runs</h3>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="p-1.5 font-medium">When</th>
                  <th className="p-1.5 font-medium">Mode</th>
                  <th className="p-1.5 font-medium">Status</th>
                  <th className="p-1.5 text-right font-medium">Fetched</th>
                  <th className="p-1.5 text-right font-medium">New</th>
                  <th className="p-1.5 text-right font-medium">Shipped</th>
                  <th className="p-1.5 text-right font-medium">Needs mapping</th>
                  <th
                    className="p-1.5 text-right font-medium"
                    title="Orders held back because payment hasn't settled yet"
                  >
                    Unpaid
                  </th>
                  <th className="p-1.5 font-medium">Error</th>
                </tr>
              </thead>
              <tbody>
                {log.map((run) => (
                  <tr key={run.id} className="border-b border-slate-100">
                    <td className="whitespace-nowrap p-1.5 tabular-nums">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                    <td className="p-1.5">{run.mode}</td>
                    <td className="p-1.5">
                      <span
                        className={
                          run.status === "success"
                            ? "text-green-700"
                            : run.status === "running"
                              ? "text-amber-700"
                              : "text-red-600"
                        }
                      >
                        {run.status}
                      </span>
                    </td>
                    <td className="p-1.5 text-right tabular-nums">{run.fetched_count}</td>
                    <td className="p-1.5 text-right tabular-nums">{run.new_count}</td>
                    <td className="p-1.5 text-right tabular-nums">{run.shipped_count}</td>
                    <td className="p-1.5 text-right tabular-nums">{run.needs_mapping_count}</td>
                    {/* Highlighted when non-zero so the gate is visible in action. A run
                        showing everything fetched but nothing new is the signature of a
                        gate wrongly rejecting orders, which otherwise looks identical to
                        a quiet week. */}
                    <td
                      className={`p-1.5 text-right tabular-nums ${run.skipped_unpaid_count > 0 ? "font-medium text-amber-700" : ""}`}
                    >
                      {run.skipped_unpaid_count}
                    </td>
                    <td className="p-1.5 text-red-600">{run.error_message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-1.5 flex items-center justify-between text-xs text-slate-500">
            <span className="tabular-nums">
              Showing {logPage * SYNC_LOG_PAGE_SIZE + 1}–
              {Math.min(logPage * SYNC_LOG_PAGE_SIZE + log.length, logTotal)} of {logTotal.toLocaleString()}
            </span>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setLogPage((p) => Math.max(0, p - 1))}
                disabled={logPage === 0}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
              >
                Prev
              </button>
              <button
                type="button"
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
    </SettingsCard>
  );
}

function PreviewResultView({ result, label }: { result: SyncPreviewResult; label: string }) {
  return (
    <div className="rounded bg-slate-50 p-2 text-sm">
      <p className="mb-2">
        Fetched <strong>{result.fetched_count}</strong>, <strong>{result.new_count}</strong> new,{" "}
        <strong>{result.needs_mapping_count}</strong> {result.needs_mapping_count === 1 ? "line" : "lines"}{" "}
        would need SKU mapping. Nothing has been imported yet.
        {result.skipped_unpaid_count > 0 && (
          <>
            {" "}
            <strong>{result.skipped_unpaid_count}</strong> would be held back as unpaid — expand them below to
            check the marketplace's own payment fields.
          </>
        )}
      </p>
      <div className="flex flex-col gap-2">
        {result.orders.map((order) => (
          <details key={order.external_order_id} className="rounded border border-slate-200 bg-white p-2">
            <summary className="cursor-pointer">
              {order.buyer_name ?? "—"} — receipt {order.external_order_id}
              {order.already_imported && (
                <span className="ml-2 text-xs text-slate-400">(already imported)</span>
              )}
              {order.is_cancelled && <span className="ml-2 text-xs text-red-600">cancelled</span>}
              {order.is_shipped && <span className="ml-2 text-xs text-green-700">shipped</span>}
              {!order.would_import && (
                <span
                  className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
                  title="Payment hasn't settled on the marketplace — a real sync would not import this order, and it would reserve no stock."
                >
                  would not import — {order.payment_state}
                </span>
              )}
              {order.would_import && order.payment_state === "reversed" && (
                <span
                  className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
                  title="Payment was refunded or charged back — imported for the record, but it reserves no stock and needs review."
                >
                  refunded
                </span>
              )}
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
