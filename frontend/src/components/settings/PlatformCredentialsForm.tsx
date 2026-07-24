import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { platformsApi, type PlatformEnvironment } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

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
  const [expanded, setExpanded] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [publicBaseUrl, setPublicBaseUrl] = useState("");
  const [ruName, setRuName] = useState("");

  const { data } = useQuery({
    queryKey: ["platforms", platform, "credentials", environment],
    queryFn: () => platformsApi.getCredentials(platform, environment),
  });

  useEffect(() => {
    setClientId(data?.client_id ?? "");
    setPublicBaseUrl(data?.public_base_url ?? "");
    setRuName(data?.ru_name ?? "");
    // client_secret is never returned by the API (write-only) — the field starts blank
    // regardless of whether one is already stored; leaving it blank on save keeps the
    // existing secret untouched (see platform_credentials.upsert_credentials).
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: () =>
      platformsApi.updateCredentials(
        platform,
        {
          client_id: clientId,
          ...(clientSecret ? { client_secret: clientSecret } : {}),
          public_base_url: publicBaseUrl,
          ...(platform === "ebay" ? { ru_name: ruName } : {}),
        },
        environment
      ),
    onSuccess: () => {
      setClientSecret("");
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "credentials", environment] });
    },
  });

  return (
    <div className="flex flex-col gap-2 border-t border-slate-200 pt-2">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="self-start text-sm text-slate-600 underline"
        >
          {expanded ? "Hide" : "Show"} developer app credentials
        </button>
        {platform === "ebay" && (
          <div className="flex items-center gap-1 text-xs">
            <button
              type="button"
              onClick={() => onEnvironmentChange("sandbox")}
              className={`rounded px-2 py-1 ${environment === "sandbox" ? "bg-amber-100 font-medium text-amber-800" : "text-slate-500"}`}
            >
              Sandbox
            </button>
            <button
              type="button"
              onClick={() => onEnvironmentChange("production")}
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
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium">Client secret</span>
            <input
              type="password"
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
              placeholder={data?.client_secret_set ? "Leave blank to keep the current secret" : ""}
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
            />
          </label>
          {platform === "ebay" ? (
            <label className="flex flex-col gap-1">
              <span className="text-sm font-medium">RuName ({environment})</span>
              <input
                className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="Not a URL — the redirect config name from eBay's dev portal"
                value={ruName}
                onChange={(e) => setRuName(e.target.value)}
              />
            </label>
          ) : (
            <label className="flex flex-col gap-1">
              <span className="text-sm font-medium">Public base URL</span>
              <input
                className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="http://127.0.0.1:8000"
                value={publicBaseUrl}
                onChange={(e) => setPublicBaseUrl(e.target.value)}
              />
              <span className="text-xs text-slate-500">
                Used to build the OAuth redirect URI — must match what's registered with Etsy.
              </span>
            </label>
          )}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="self-start rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {saveMutation.isPending ? "Saving…" : "Save credentials"}
          </button>
          <ErrorBanner error={saveMutation.error} />
        </div>
      )}
    </div>
  );
}
