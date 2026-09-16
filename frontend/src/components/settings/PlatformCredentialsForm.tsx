import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { platformsApi, type PlatformEnvironment } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";

interface CredentialsForm {
  clientId: string;
  clientSecret: string;
  publicBaseUrl: string;
  ruName: string;
}

const EMPTY_CREDENTIALS: CredentialsForm = {
  clientId: "",
  clientSecret: "",
  publicBaseUrl: "",
  ruName: "",
};

// A packaged desktop install has no build-time secret-injection pipeline and no `.env`
// file a user would ever edit by hand — the developer-app Client ID/Secret registered
// with each marketplace has to be entered here instead. See
// docs/plan-marketplace-integrations.md Section 1a for why this exists.
export function PlatformCredentialsForm({
  platform,
  environment,
}: {
  platform: ListingPlatform;
  environment: PlatformEnvironment;
}) {
  const queryClient = useQueryClient();

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
    [data],
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
        environment,
      ),
    onSuccess: () => {
      // Blank the secret again: it was write-only going out, so keeping it on screen would imply
      // it's readable, and it must not count towards dirty afterwards.
      markSaved({ ...form, clientSecret: "" });
      queryClient.invalidateQueries({
        queryKey: ["platforms", platform, "credentials", environment],
      });
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  return (
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
  );
}
