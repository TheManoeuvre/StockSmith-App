import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { appSettingsApi } from "../../api/appSettings";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * First launch: "what do you make?". The answer decides which material categories, units and
 * example material types a fresh install starts with — see backend/app/services/business_presets.py
 * and docs/plan-onboarding.md §4.
 *
 * Shown only when the backend reports `business_type: null`, i.e. a fresh install that hasn't
 * chosen. Existing installs were stamped by the migration that added the column and never see it.
 * A strict `=== null` check, not falsy: a failed or unexpected response must never put a
 * full-screen overlay over a working app.
 *
 * Like MaintenanceOverlay, it stands down on Settings — that's where "restore a backup instead"
 * leads, and restoring replaces the database (and with it the answer to this question).
 */
export function BusinessTypePicker() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const onSettings = useRouterState({ select: (state) => state.location.pathname.startsWith("/settings") });
  const { data } = useQuery({ queryKey: ["business-type"], queryFn: appSettingsApi.getBusinessType });
  const [selected, setSelected] = useState<string | null>(null);

  const choose = useMutation({
    mutationFn: (key: string) => appSettingsApi.setBusinessType(key),
    // Categories, units and material types all just changed, and every page caches some of them.
    onSuccess: () => queryClient.invalidateQueries(),
  });

  if (!data || data.business_type !== null || !data.can_choose || onSettings) return null;

  const preview = data.presets.find((p) => p.key === selected);

  return (
    <div className="fixed inset-0 z-40 overflow-y-auto bg-slate-900/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="business-type-title"
        className="mx-auto my-8 flex max-w-3xl flex-col gap-5 rounded-lg bg-white p-6 shadow-lg"
      >
        <div className="flex flex-col gap-1">
          <h1 id="business-type-title" className="text-xl font-semibold text-slate-900">
            Welcome to StockSmith
          </h1>
          <p className="text-sm text-slate-600">
            What do you make? We'll set up material categories that fit your business. You can rename, add or
            remove any of them later in Settings › Lists.
          </p>
        </div>

        <fieldset className="grid gap-2 sm:grid-cols-2">
          <legend className="sr-only">Business type</legend>
          {data.presets.map((preset) => {
            const isSelected = preset.key === selected;
            return (
              <label
                key={preset.key}
                className={`flex cursor-pointer flex-col gap-0.5 rounded-md border p-3 text-left ${
                  isSelected ? "border-slate-900 bg-slate-50 ring-1 ring-slate-900" : "border-slate-200 hover:border-slate-400"
                }`}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="business-type"
                    value={preset.key}
                    checked={isSelected}
                    onChange={() => setSelected(preset.key)}
                    className="accent-slate-900"
                  />
                  <span className="text-sm font-medium text-slate-900">{preset.label}</span>
                </span>
                <span className="pl-5 text-xs text-slate-500">{preset.description}</span>
              </label>
            );
          })}
        </fieldset>

        {preview && (
          <div className="flex flex-col gap-1.5 rounded-md bg-slate-50 p-3 text-sm" aria-live="polite">
            <PreviewRow label="Material categories" values={preview.categories} capitalise />
            <PreviewRow label="Units" values={preview.units} />
            {preview.material_types.length > 0 && (
              <PreviewRow label="Example material types" values={preview.material_types} />
            )}
          </div>
        )}

        <ErrorBanner error={choose.error} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            className="text-sm text-slate-600 underline hover:text-slate-900"
            onClick={() => navigate({ to: "/settings", search: { page: "backup-restore" } })}
          >
            Moving from another computer? Restore a backup instead
          </button>
          <button
            type="button"
            disabled={!selected || choose.isPending}
            onClick={() => selected && choose.mutate(selected)}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {choose.isPending ? "Setting up…" : "Set up StockSmith"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PreviewRow({ label, values, capitalise }: { label: string; values: string[]; capitalise?: boolean }) {
  return (
    <p>
      <span className="font-medium text-slate-700">{label}: </span>
      <span className={`text-slate-600 ${capitalise ? "[&>span]:capitalize" : ""}`}>
        {values.map((v, i) => (
          <span key={v}>
            {v}
            {i < values.length - 1 ? ", " : ""}
          </span>
        ))}
      </span>
    </p>
  );
}
