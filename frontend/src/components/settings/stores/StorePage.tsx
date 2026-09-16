import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type PlatformEnvironment } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { openExternalUrl } from "../../../lib/tauri";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../../../lib/platforms";
import { useShopIconUrl } from "../../../hooks/useShopIconUrl";
import { DirtyPath } from "../../../hooks/useDirtyRegistry";
import { ErrorBanner } from "../../common/ErrorBanner";
import { Disclosure } from "../Disclosure";
import { EtsyProfileProposalsPanel } from "../EtsyProfileProposalsPanel";
import { ListingProfiles } from "../ListingProfiles";
import { PlatformLimitsEditor } from "../PlatformLimitsEditor";
import { DeveloperAppCard } from "./DeveloperAppCard";
import { OrderSyncCard } from "./OrderSyncCard";
import { StockPushesCard } from "./StockPushesCard";
import { StoreToolsCard } from "./StoreToolsCard";
import { formatRelative, stateChip, useStoreHealth } from "./useStoreHealth";

/**
 * Everything about one store, one column, ordered by how often it's needed: health first,
 * then the daily operations (order sync, stock pushes), then what's configured occasionally
 * (listing profiles), then the migration tools, and last the developer app that was set up
 * once. The old page interleaved all of these in the order they were built.
 */
export function StorePage({ platform }: { platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const health = useStoreHealth(platform);
  const { status, summary, connected, state, failingPushCount } = health;
  const chip = stateChip(state, failingPushCount);
  const iconUrl = useShopIconUrl(platform, status?.has_shop_icon ?? false, status?.connected_at ?? null);

  // Which environment to connect/edit credentials against — only meaningful for eBay
  // (the toggle only renders there); Etsy always uses "production". Local UI state, not
  // server state: it picks which environment's credentials the Developer app card edits and
  // which one "Connect" targets, independent of whatever's actually connected.
  const [environment, setEnvironment] = useState<PlatformEnvironment>("production");

  const invalidateStatus = () => {
    queryClient.invalidateQueries({
      queryKey: ["platforms", platform, "status"],
    });
    queryClient.invalidateQueries({ queryKey: ["platforms", "sync-summary"] });
  };
  const connectMutation = useMutation({
    mutationFn: async () => {
      const { authorize_url } = await platformsApi.connect(platform, environment);
      await openExternalUrl(authorize_url);
    },
  });
  const disconnectMutation = useMutation({
    mutationFn: () => platformsApi.disconnect(platform),
    onSuccess: invalidateStatus,
  });

  const connectLabel = connectMutation.isPending
    ? "Opening…"
    : `${state === "reconnect" ? "Reconnect" : "Connect"}${environment === "sandbox" ? " to sandbox" : ""}`;

  return (
    // Nested so each platform's editors sit under stores-sync/<platform>/…, which is what lets
    // a prefix veto target one store without catching the other. The trailing-slash convention in
    // isDirtyUnder keeps "ebay/" from matching a hypothetical "ebay-sandbox/".
    <DirtyPath segment="stores-sync">
      <DirtyPath segment={platform}>
        <div className="flex flex-col gap-4">
          <section
            aria-label={`${label} connection`}
            className={`rounded-[9px] border border-slate-200 border-t-[3px] bg-white p-4 ${PLATFORM_COLORS[platform].accent}`}
            style={{ boxShadow: "0 1px 2px rgba(15,23,42,.04)" }}
          >
            <div className="flex items-center gap-3">
              {connected && iconUrl && (
                <img src={iconUrl} alt="" className="h-9 w-9 rounded-full object-cover" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h2 className="font-medium">{label}</h2>
                  <span className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold ${chip.className}`}>
                    {chip.label}
                  </span>
                  {connected && status?.environment === "sandbox" && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800">
                      Sandbox
                    </span>
                  )}
                </div>
                <p className="text-[12.5px] text-slate-500">
                  {connected
                    ? [
                        status?.shop_name ?? (status?.account_id ? `Account ${status.account_id}` : null),
                        status?.last_sync_attempt_at
                          ? `synced ${formatRelative(status.last_sync_attempt_at)}`
                          : "never synced",
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : `Connect to import ${label} orders and keep listed quantities in step with stock.`}
                </p>
              </div>
              {connected && state !== "reconnect" ? (
                <button
                  type="button"
                  onClick={() => disconnectMutation.mutate()}
                  disabled={disconnectMutation.isPending}
                  className="h-7 rounded-md border border-red-200 px-2.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Disconnect
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => connectMutation.mutate()}
                  disabled={connectMutation.isPending}
                  className="h-7 rounded-md bg-slate-900 px-2.5 text-xs font-semibold text-white disabled:opacity-50"
                >
                  {connectLabel}
                </button>
              )}
            </div>
            <ErrorBanner error={connectMutation.error ?? disconnectMutation.error} />
            {state === "reconnect" && (
              <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">
                {status?.needs_reconnect_reason ??
                  `Reconnect ${label} to grant StockSmith the permissions it now needs.`}
              </p>
            )}
            {state === "sync-failed" && (
              <p className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">
                The last order sync failed
                {status?.last_sync_error ? `: ${status.last_sync_error}` : "."} Details under Order sync
                below.
              </p>
            )}
            {state === "pushes-failing" && (
              <p className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">
                {failingPushCount === 1 ? "One listing isn't" : `${failingPushCount} listings aren't`}{" "}
                receiving stock updates. Details under Stock pushes below.
              </p>
            )}
          </section>

          {connected && (
            <>
              <OrderSyncCard platform={platform} status={status} />
              <StockPushesCard platform={platform} summary={summary} failingPushCount={failingPushCount} />
              <ListingProfiles platform={platform}>
                {platform === "etsy" && (
                  <Disclosure
                    title="Suggest profiles from Etsy"
                    summary="group your existing listings into profiles"
                  >
                    <EtsyProfileProposalsPanel />
                  </Disclosure>
                )}
                <PlatformLimitsEditor platform={platform} />
              </ListingProfiles>
              <StoreToolsCard platform={platform} status={status} />
            </>
          )}
          <DeveloperAppCard
            platform={platform}
            environment={environment}
            onEnvironmentChange={setEnvironment}
          />
        </div>
      </DirtyPath>
    </DirtyPath>
  );
}
