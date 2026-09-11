import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  notificationsApi,
  type NotificationCategory,
  type NotificationDeliveryMode,
  type NotificationSettings as NotificationSettingsValue,
  type SummaryFrequency,
} from "../../api/notifications";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { FieldRow } from "../common/FieldRow";
import { SaveButton } from "../common/SaveButton";
import { SegmentedControl } from "../common/SegmentedControl";
import { Switch } from "../common/Switch";
import { SettingsCard } from "./SettingsCard";

// Display order for the 9 configurable alert types — grouped by what they're about rather
// than the backend's alphabetical-by-enum-value ordering, which interleaves unrelated
// concerns (the two marketplace-API-limit types would otherwise sit next to backup alerts).
const ALERT_TYPE_ORDER: NotificationCategory[] = [
  "marketplace_sync_failure",
  "marketplace_api_soft_limit",
  "marketplace_api_hard_limit",
  "material_forecast_warning",
  "material_forecast_critical",
  "order_unfulfillable",
  "pending_order_threshold",
  "backup_failed",
  "secondary_backup_unreachable",
];

const ALERT_TYPE_LABELS: Record<NotificationCategory, string> = {
  marketplace_sync_failure: "Marketplace sync failed",
  marketplace_api_soft_limit: "Marketplace API usage — approaching limit",
  marketplace_api_hard_limit: "Marketplace API usage — limit hit",
  material_forecast_warning: "Material running low (warning)",
  material_forecast_critical: "Material running low (critical)",
  order_unfulfillable: "Order can't be fulfilled",
  pending_order_threshold: "Pending orders over threshold",
  backup_failed: "Backup failed",
  secondary_backup_unreachable: "Secondary backup location unreachable",
  daily_summary: "Order summary",
};

const DELIVERY_MODE_OPTIONS: { value: NotificationDeliveryMode; label: string }[] = [
  { value: "immediate", label: "Right away" },
  { value: "digest", label: "Digest" },
  { value: "off", label: "Off" },
];

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

interface FormAlertType {
  alert_type: NotificationCategory;
  enabled: boolean;
  delivery_mode: NotificationDeliveryMode;
}

interface Form {
  windowsNotificationsEnabled: boolean;
  pushoverEnabled: boolean;
  quietHoursEnabled: boolean;
  quietHoursStart: number;
  quietHoursEnd: number;
  dailySummaryEnabled: boolean;
  dailySummaryFrequency: SummaryFrequency;
  dailySummaryHourLocal: number;
  dailySummaryDayOfWeek: number;
  pendingOrderThreshold: string;
  alertTypes: FormAlertType[];
}

function toForm(settings: NotificationSettingsValue): Form {
  return {
    windowsNotificationsEnabled: settings.windows_notifications_enabled,
    pushoverEnabled: settings.pushover_enabled,
    quietHoursEnabled: settings.quiet_hours_enabled,
    quietHoursStart: settings.quiet_hours_start,
    quietHoursEnd: settings.quiet_hours_end,
    dailySummaryEnabled: settings.daily_summary_enabled,
    dailySummaryFrequency: settings.daily_summary_frequency,
    dailySummaryHourLocal: settings.daily_summary_hour_local,
    dailySummaryDayOfWeek: settings.daily_summary_day_of_week ?? 0,
    pendingOrderThreshold: String(settings.pending_order_threshold),
    alertTypes: settings.alert_types,
  };
}

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function NotificationSettings() {
  const queryClient = useQueryClient();
  const {
    data: settings,
    isLoading,
    error: settingsError,
  } = useQuery({ queryKey: ["settings", "notifications"], queryFn: notificationsApi.getSettings });

  const {
    value: form,
    setValue: setForm,
    isDirty,
    isSeeded,
    markSaved,
  } = useEditableCopy<Form | null>({
    key: "notifications",
    label: "Notification settings",
    initial: null,
    seed: settings ? toForm(settings) : undefined,
    seedKey: "settings",
  });

  const [pushoverKeyInput, setPushoverKeyInput] = useState("");
  const [replacingKey, setReplacingKey] = useState(false);
  const [pushoverTestResult, setPushoverTestResult] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: (value: Form) =>
      notificationsApi.updateSettings({
        windows_notifications_enabled: value.windowsNotificationsEnabled,
        pushover_enabled: value.pushoverEnabled,
        pushover_user_key: pushoverKeyInput.trim() || undefined,
        quiet_hours_enabled: value.quietHoursEnabled,
        quiet_hours_start: value.quietHoursStart,
        quiet_hours_end: value.quietHoursEnd,
        daily_summary_enabled: value.dailySummaryEnabled,
        daily_summary_frequency: value.dailySummaryFrequency,
        daily_summary_hour_local: value.dailySummaryHourLocal,
        daily_summary_day_of_week: value.dailySummaryFrequency === "weekly" ? value.dailySummaryDayOfWeek : null,
        pending_order_threshold: Number(value.pendingOrderThreshold) || 1,
        alert_types: value.alertTypes,
      }),
    onSuccess: (saved) => {
      markSaved(toForm(saved));
      setPushoverKeyInput("");
      setReplacingKey(false);
      queryClient.invalidateQueries({ queryKey: ["settings", "notifications"] });
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  const testMutation = useMutation({
    mutationFn: notificationsApi.testPushover,
    onSuccess: (result) => setPushoverTestResult(result.success ? "Test sent — check your phone." : result.reason ?? "Test failed."),
  });

  const clearMutation = useMutation({
    mutationFn: notificationsApi.clearPushover,
    onSuccess: () => {
      setPushoverKeyInput("");
      setReplacingKey(false);
      setPushoverTestResult(null);
      queryClient.invalidateQueries({ queryKey: ["settings", "notifications"] });
    },
  });

  if (isLoading || !isSeeded || !form) {
    return (
      <SettingsCard title="Notifications">
        {settingsError ? (
          <ErrorBanner error={settingsError} />
        ) : (
          <p className="text-sm text-slate-500">Loading…</p>
        )}
      </SettingsCard>
    );
  }

  const setField = <K extends keyof Form>(field: K, next: Form[K]) =>
    setForm((prev) => (prev ? { ...prev, [field]: next } : prev));

  const setAlertType = (alertType: NotificationCategory, patch: Partial<FormAlertType>) =>
    setForm((prev) =>
      prev
        ? {
            ...prev,
            alertTypes: prev.alertTypes.map((row) => (row.alert_type === alertType ? { ...row, ...patch } : row)),
          }
        : prev
    );

  const orderedAlertTypes = ALERT_TYPE_ORDER.map((type) => form.alertTypes.find((row) => row.alert_type === type)).filter(
    (row): row is FormAlertType => !!row
  );

  return (
    <div className="flex flex-col gap-4">
      <SettingsCard
        title="Delivery channels"
        help="Every alert is logged here in StockSmith regardless of these toggles — they only control whether it also reaches you outside the app."
      >
        <div className="flex items-center gap-2 text-sm">
          <Switch
            id="windows-notifications-toggle"
            checked={form.windowsNotificationsEnabled}
            onChange={(checked) => setField("windowsNotificationsEnabled", checked)}
          />
          <label htmlFor="windows-notifications-toggle">Windows notifications</label>
        </div>
        <p className="pl-11 text-xs text-slate-500">
          A native Windows toast while StockSmith is running, for anything marked "Right away" below.
        </p>

        <div className="mt-1 flex items-center gap-2 text-sm">
          <Switch
            id="pushover-toggle"
            checked={form.pushoverEnabled}
            onChange={(checked) => setField("pushoverEnabled", checked)}
          />
          <label htmlFor="pushover-toggle">Mobile push (via Pushover)</label>
        </div>
        <p className="pl-11 text-xs text-slate-500">
          Sent to your phone through the free{" "}
          <a
            href="https://pushover.net"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            Pushover
          </a>{" "}
          app, as long as StockSmith is running in the background — it doesn't matter whether the window
          is open, minimised, or closed to the tray.
        </p>

        <div className="ml-11 flex flex-col gap-2">
          {settings?.pushover_user_key_masked && !replacingKey && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-slate-600">
                Key ending in <span className="font-mono">{settings.pushover_user_key_masked.slice(-4)}</span>
              </span>
              <button type="button" onClick={() => setReplacingKey(true)} className="text-xs text-slate-500 underline">
                Replace
              </button>
              <button
                type="button"
                onClick={() => clearMutation.mutate()}
                disabled={clearMutation.isPending}
                className="text-xs text-red-600 underline disabled:opacity-50"
              >
                Remove
              </button>
            </div>
          )}
          {(!settings?.pushover_user_key_masked || replacingKey) && (
            <FieldRow label="Pushover user key">
              <input
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="e.g. u1a2b3c4d5e6f7g8h9i0j1k2l3m4n5"
                value={pushoverKeyInput}
                onChange={(e) => setPushoverKeyInput(e.target.value)}
              />
            </FieldRow>
          )}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending || !settings?.pushover_user_key_masked}
              className="w-fit rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {testMutation.isPending ? "Sending…" : "Send test notification"}
            </button>
            {pushoverTestResult && <span className="text-xs text-slate-500">{pushoverTestResult}</span>}
          </div>
        </div>
      </SettingsCard>

      <SettingsCard
        title="Quiet hours"
        help="Suppresses Windows toasts and Pushover pushes during this window — the alert still lands in-app. Anything urgent enough to be marked immediate below (like the order summary) still gets through regardless."
      >
        <div className="flex items-center gap-2 text-sm">
          <Switch
            id="quiet-hours-toggle"
            checked={form.quietHoursEnabled}
            onChange={(checked) => setField("quietHoursEnabled", checked)}
          />
          <label htmlFor="quiet-hours-toggle">Enable quiet hours</label>
        </div>
        <div className="flex flex-col gap-2">
          <FieldRow label="From">
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={form.quietHoursStart}
              disabled={!form.quietHoursEnabled}
              onChange={(e) => setField("quietHoursStart", Number(e.target.value))}
            >
              {HOURS.map((hour) => (
                <option key={hour} value={hour}>
                  {hourLabel(hour)}
                </option>
              ))}
            </select>
          </FieldRow>
          <FieldRow label="Until">
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={form.quietHoursEnd}
              disabled={!form.quietHoursEnabled}
              onChange={(e) => setField("quietHoursEnd", Number(e.target.value))}
            >
              {HOURS.map((hour) => (
                <option key={hour} value={hour}>
                  {hourLabel(hour)}
                </option>
              ))}
            </select>
          </FieldRow>
        </div>
      </SettingsCard>

      <SettingsCard title="Order summary" help="A recap of revenue, profit, and items shipped, sent on a schedule.">
        <div className="flex items-center gap-2 text-sm">
          <Switch
            id="daily-summary-toggle"
            checked={form.dailySummaryEnabled}
            onChange={(checked) => setField("dailySummaryEnabled", checked)}
          />
          <label htmlFor="daily-summary-toggle">Send order summary</label>
        </div>
        <div className="flex flex-col gap-2">
          <FieldRow label="Frequency">
            <SegmentedControl
              ariaLabel="Summary frequency"
              value={form.dailySummaryFrequency}
              onChange={(next) => setField("dailySummaryFrequency", next)}
              options={[
                { value: "daily" as const, label: "Daily" },
                { value: "weekly" as const, label: "Weekly" },
              ]}
            />
          </FieldRow>
          {form.dailySummaryFrequency === "weekly" && (
            <FieldRow label="Day">
              <select
                className="rounded border border-slate-300 px-2 py-1"
                value={form.dailySummaryDayOfWeek}
                onChange={(e) => setField("dailySummaryDayOfWeek", Number(e.target.value))}
              >
                {WEEKDAYS.map((day, index) => (
                  <option key={day} value={index}>
                    {day}
                  </option>
                ))}
              </select>
            </FieldRow>
          )}
          <FieldRow label="Time">
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={form.dailySummaryHourLocal}
              onChange={(e) => setField("dailySummaryHourLocal", Number(e.target.value))}
            >
              {HOURS.map((hour) => (
                <option key={hour} value={hour}>
                  {hourLabel(hour)}
                </option>
              ))}
            </select>
          </FieldRow>
        </div>
      </SettingsCard>

      <SettingsCard
        title="Alert types"
        help="What StockSmith watches for, and how each one reaches you: right away, batched into the next digest, or not at all."
      >
        <div className="flex flex-col divide-y divide-slate-100">
          {orderedAlertTypes.map((row) => (
            <div key={row.alert_type} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
              <span className="text-sm">{ALERT_TYPE_LABELS[row.alert_type]}</span>
              <div className="flex items-center gap-3">
                {row.alert_type === "pending_order_threshold" && (
                  <label className="flex items-center gap-1 text-xs text-slate-500">
                    over
                    <input
                      type="number"
                      min="1"
                      className="w-16 rounded border border-slate-300 px-1.5 py-1 text-right tabular-nums"
                      value={form.pendingOrderThreshold}
                      disabled={row.delivery_mode === "off"}
                      onChange={(e) => setField("pendingOrderThreshold", e.target.value)}
                    />
                    orders
                  </label>
                )}
                <SegmentedControl
                  ariaLabel={`${ALERT_TYPE_LABELS[row.alert_type]} delivery`}
                  value={row.delivery_mode}
                  onChange={(next) => setAlertType(row.alert_type, { delivery_mode: next, enabled: next !== "off" })}
                  options={DELIVERY_MODE_OPTIONS}
                />
              </div>
            </div>
          ))}
        </div>
      </SettingsCard>

      {/* One card of Notification settings is one PUT — every section above shares this
          single Save, rather than each card managing its own request. Sticky so it stays
          reachable while the Alert types list scrolls past a full page of rows. */}
      <div className="sticky bottom-0 flex items-center gap-2 rounded-[9px] border border-slate-200 bg-white/95 p-3 backdrop-blur">
        <SaveButton
          isDirty={isDirty}
          enabledWhen={isDirty || pushoverKeyInput.trim().length > 0}
          isPending={saveMutation.isPending}
          status={saveStatus}
          onClick={() => saveMutation.mutate(form)}
        >
          Save
        </SaveButton>
        <ErrorBanner error={saveMutation.error} />
      </div>
    </div>
  );
}
