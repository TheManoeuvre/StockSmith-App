import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { appSettingsApi, type ForecastSettings as ForecastSettingsValue } from "../../api/appSettings";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { FieldRow } from "../common/FieldRow";
import { SaveButton } from "../common/SaveButton";
import { SettingsCard } from "./SettingsCard";

/**
 * Buffered rather than auto-saved, for two reasons that both apply here.
 *
 * These are numbers typed into free-text inputs, so an auto-save would fire per keystroke on a
 * half-entered value. And they carry a cross-field constraint — the backend rejects
 * critical > warning (routers/fee_config.py) — so a valid end state routinely passes through an
 * invalid intermediate: raising both thresholds gets rejected or not depending purely on which
 * field you happen to touch first.
 */
export function ForecastSettings() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings", "forecast-settings"],
    queryFn: appSettingsApi.getForecastSettings,
  });

  const {
    value: form,
    setValue: setForm,
    isDirty,
    isSeeded,
    markSaved,
  } = useEditableCopy<ForecastSettingsValue | null>({
    key: "general/forecast",
    label: "Materials forecasting",
    initial: null,
    seed: data,
    // A constant: there is only ever one settings row, so the only thing that could re-seed is a
    // refetch — which is precisely what must not clobber an in-progress edit.
    seedKey: "settings",
  });

  const updateMutation = useMutation({
    mutationFn: (settings: ForecastSettingsValue) => appSettingsApi.updateForecastSettings(settings),
    onSuccess: (settings) => {
      // Baseline from what was stored, not what was sent — the server normalises "6" to "6.00",
      // which would otherwise leave the form looking dirty the moment it re-rendered.
      markSaved(settings);
      queryClient.invalidateQueries({ queryKey: ["settings", "forecast-settings"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });
  const saveStatus = useSaveStatus(updateMutation.status);

  if (!isSeeded || !form) return null;

  const setField = <K extends keyof ForecastSettingsValue>(field: K, next: ForecastSettingsValue[K]) =>
    setForm((prev) => (prev ? { ...prev, [field]: next } : prev));

  const numberInput = "w-28 rounded border border-slate-300 px-2 py-1 text-right tabular-nums";

  return (
    <SettingsCard
      title="Materials forecasting"
      help={
        <>
          Controls the "Time to stockout" figures on the dashboard — how far back consumption
          history is averaged, and the two weeks-of-cover thresholds that decide when a material
          shows up as a warning or critical alert.
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <FieldRow label="Warning threshold (weeks)">
          <input
            type="number"
            min="0"
            step="0.5"
            className={numberInput}
            value={form.forecast_warning_weeks}
            onChange={(e) => setField("forecast_warning_weeks", e.target.value)}
          />
        </FieldRow>
        <FieldRow label="Critical threshold (weeks)">
          <input
            type="number"
            min="0"
            step="0.5"
            className={numberInput}
            value={form.forecast_critical_weeks}
            onChange={(e) => setField("forecast_critical_weeks", e.target.value)}
          />
        </FieldRow>
        <FieldRow label="Lookback window (weeks)">
          <input
            type="number"
            min="1"
            step="1"
            className={numberInput}
            value={form.forecast_lookback_weeks}
            onChange={(e) => setField("forecast_lookback_weeks", Number(e.target.value))}
          />
        </FieldRow>
        <FieldRow label="Default lead time (weeks)">
          <input
            type="number"
            min="0"
            step="0.5"
            className={numberInput}
            value={form.default_lead_time_weeks}
            onChange={(e) => setField("default_lead_time_weeks", e.target.value)}
          />
        </FieldRow>
      </div>
      <p className="text-xs text-slate-500">
        Default lead time estimates when an on-order purchase will arrive if it wasn't given its own
        expected arrival date, and pushes a material's reorder point that far ahead of stockout so
        there's time to restock. A supplier with its own lead time set overrides this for its materials.
      </p>
      <div className="flex items-center gap-2">
        <SaveButton
          isDirty={isDirty}
          isPending={updateMutation.isPending}
          status={saveStatus}
          onClick={() => updateMutation.mutate(form)}
        >
          Save
        </SaveButton>
      </div>
      <ErrorBanner error={updateMutation.error} />
    </SettingsCard>
  );
}
