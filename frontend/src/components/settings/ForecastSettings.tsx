import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { appSettingsApi, type ForecastSettings as ForecastSettingsValue } from "../../api/appSettings";
import { ErrorBanner } from "../common/ErrorBanner";

export function ForecastSettings() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings", "forecast-settings"],
    queryFn: appSettingsApi.getForecastSettings,
  });

  const [form, setForm] = useState<ForecastSettingsValue | null>(null);
  useEffect(() => {
    if (data && !form) setForm(data);
  }, [data, form]);

  const updateMutation = useMutation({
    mutationFn: (settings: ForecastSettingsValue) => appSettingsApi.updateForecastSettings(settings),
    onSuccess: (settings) => {
      queryClient.invalidateQueries({ queryKey: ["settings", "forecast-settings"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      setForm(settings);
    },
  });

  if (!form) return null;

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Materials forecasting</p>
        <p className="text-sm text-slate-500">
          Controls the "Time to stockout" figures on the dashboard — how far back consumption history is
          averaged, and the two weeks-of-cover thresholds that decide when a material shows up as a warning
          or critical alert.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:max-w-md">
        <label className="flex flex-col gap-1 text-sm">
          Warning threshold (weeks)
          <input
            type="number"
            min="0"
            step="0.5"
            className="rounded border border-slate-300 px-2 py-1"
            value={form.forecast_warning_weeks}
            onChange={(e) => setForm({ ...form, forecast_warning_weeks: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Critical threshold (weeks)
          <input
            type="number"
            min="0"
            step="0.5"
            className="rounded border border-slate-300 px-2 py-1"
            value={form.forecast_critical_weeks}
            onChange={(e) => setForm({ ...form, forecast_critical_weeks: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Lookback window (weeks)
          <input
            type="number"
            min="1"
            step="1"
            className="rounded border border-slate-300 px-2 py-1"
            value={form.forecast_lookback_weeks}
            onChange={(e) => setForm({ ...form, forecast_lookback_weeks: Number(e.target.value) })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Default lead time (weeks)
          <input
            type="number"
            min="0"
            step="0.5"
            className="rounded border border-slate-300 px-2 py-1"
            value={form.default_lead_time_weeks}
            onChange={(e) => setForm({ ...form, default_lead_time_weeks: e.target.value })}
          />
        </label>
      </div>
      <p className="text-xs text-slate-500">
        Default lead time estimates when an on-order purchase will arrive if it wasn't given its own expected
        arrival date — used so a distant order doesn't mask a material about to run out.
      </p>
      <button
        onClick={() => updateMutation.mutate(form)}
        className="w-fit rounded border border-slate-300 bg-slate-100 px-3 py-1 text-sm"
      >
        Save
      </button>
      <ErrorBanner error={updateMutation.error} />
    </div>
  );
}
