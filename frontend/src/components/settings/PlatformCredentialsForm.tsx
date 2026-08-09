import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { platformsApi, type PlatformEnvironment } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";

interface CredentialsForm {
  clientId: string;
  clientSecret: string;
  publicBaseUrl: string;
  ruName: string;
}

const EMPTY_CREDENTIALS: CredentialsForm = { clientId: "", clientSecret: "", publicBaseUrl: "", ruName: "" };

// A packaged desktop install has no build-time secret-injection pipeline and no `.env`
// file a user would ever edit by hand — the developer-app Client ID/Secret registered
// with each marketplace has to be entered here instead. See
// docs/plan-marketplace-integrations.md Section 1a for why this exists.
export function PlatformCredentialsForm({
  platform,
  environment,
  onEnvironmentChange,
}: {
  platform: ListingPlatform;
  environment: PlatformEnvironment;
  onEnvironmentChange: (environment: PlatformEnvironment) => void;
}) {
  const queryClient = useQueryClient();
  const guard = useGuard();
  const [expanded, setExpanded] = useState(false);

  const { data } = useQuery({
    queryKey: ["platforms", platform, "credentials", environment],
    queryFn: () => platformsApi.getCredentials(platform, environment),
  });

  // client_secret is never returned by the API (write-only), so it always seeds blank whether or
  // not one is stored. Leaving it blank on save keeps the existing secret untouched — see
  // platform_credentials.upsert_credentials.
  const seed = useMemo<CredentialsForm | undefined>(
    () =>
      data
        ? {
            clientId: data.client_id ?? "",
            clientSecret: "",
            publicBaseUrl: data.public_base_url ?? "",
            ruName: data.ru_name ?? "",
          }
        : undefined,
    [data]
  );

  const {
    value: form,
    setValue: setForm,
    isDirty,
    markSaved,
  } = useEditableCopy<CredentialsForm>({
    key: "credentials",
    label: `${platform === "ebay" ? "eBay" : "Etsy"} developer credentials`,
    initial: EMPTY_CREDENTIALS,
    seed,
    // The environment toggle genuinely swaps which credentials are being edited, so it must
    // re-seed — that's exactly what seedKey is for. The effect this replaces keyed on `data`
    // instead, which also re-seeded on every background refetch and silently discarded whatever
    // was half-typed at the time.
    seedKey: `${platform}:${environment}`,
  });

  const setField = <K extends keyof CredentialsForm>(field: K, next: CredentialsForm[K]) =>
    setForm((prev) => ({ ...prev, [field]: next }));

  const saveMutation = useMutation({
    mutationFn: () =>
      platformsApi.updateCredentials(
        platform,
        {
          client_id: form.clientId,
          ...(form.clientSecret ? { client_secret: form.clientSecret } : {}),
          public_base_url: form.publicBaseUrl,
          ...(platform === "ebay" ? { ru_name: form.ruName } : {}),
        },
        environment
      ),
    onSuccess: () => {
      // Blank the secret again: it was write-only going out, so keeping it on screen would imply
      // it's readable, and it must not count towards dirty afterwards.
      markSaved({ ...form, clientSecret: "" });
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "credentials", environment] });
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  return (
    <div className="flex flex-col gap-2 border-t border-slate-200 pt-2">
      <div className="flex items-center justify-between">
        <button
          type="button"
          // Collapsing unmounts the form, and a React unmount can't be cancelled after the fact —
          // so the veto has to happen before the state setter runs. Same pattern as VariantEditor.
          onClick={() => guard.attempt(() => setExpanded((v) => !v), { prefix: "credentials" })}
          className="self-start text-sm text-slate-600 underline"
        >
          {expanded ? "Hide" : "Show"} developer app credentials
        </button>
        {platform === "ebay" && (
          <div className="flex items-center gap-1 text-xs">
            <button
              type="button"
              // Switching environment re-seeds the form, discarding edits just as surely as
              // collapsing it would.
              onClick={() => guard.attempt(() => onEnvironmentChange("sandbox"), { prefix: "credentials" })}
              className={`rounded px-2 py-1 ${environment === "sandbox" ? "bg-amber-100 font-medium text-amber-800" : "text-slate-500"}`}
            >
              Sandbox
            </button>
            <button
              type="button"
              onClick={() => guard.attempt(() => onEnvironmentChange("production"), { prefix: "credentials" })}
              className={`rounded px-2 py-1 ${environment === "production" ? "bg-slate-900 font-medium text-white" : "text-slate-500"}`}
            >
              Production
            </button>
          </div>
        )}
      </div>
      {!expanded && (
        <p className="text-xs text-slate-500">
          Client ID {data?.client_id ? <span className="font-mono">{data.client_id}</span> : "not set"} · Secret{" "}
          {data?.client_secret_set ? "configured" : "not set"}
          {platform === "ebay" && ` (${environment})`}
        </p>
      )}

      {expanded && (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium">Client ID</span>
            <input
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
              value={form.clientId}
              onChange={(e) => setField("clientId", e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium">Client secret</span>
            <input
              type="password"
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
              placeholder={data?.client_secret_set ? "Leave blank to keep the current secret" : ""}
              value={form.clientSecret}
              onChange={(e) => setField("clientSecret", e.target.value)}
            />
          </label>
          {platform === "ebay" ? (
            <label className="flex flex-col gap-1">
              <span className="text-sm font-medium">RuName ({environment})</span>
              <input
                className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="Not a URL — the redirect config name from eBay's dev portal"
                value={form.ruName}
                onChange={(e) => setField("ruName", e.target.value)}
              />
            </label>
          ) : (
            <label className="flex flex-col gap-1">
              <span className="text-sm font-medium">Public base URL</span>
              <input
                className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="http://127.0.0.1:8000"
                value={form.publicBaseUrl}
                onChange={(e) => setField("publicBaseUrl", e.target.value)}
              />
              <span className="text-xs text-slate-500">
                Used to build the OAuth redirect URI — must match what's registered with Etsy.
              </span>
            </label>
          )}
          <div className="flex items-center gap-2">
            <SaveButton
              isDirty={isDirty}
              isPending={saveMutation.isPending}
              status={saveStatus}
              onClick={() => saveMutation.mutate()}
            >
              Save credentials
            </SaveButton>
          </div>
          <ErrorBanner error={saveMutation.error} />
        </div>
      )}
    </div>
  );
}
