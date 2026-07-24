import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getSettings, openExternalUrl, saveSettings } from "../lib/tauri";
import { healthCheck } from "../api/client";
import { platformsApi, type PlatformEnvironment } from "../api/platforms";
import type { ListingPlatform } from "../api/types";
import { PLATFORM_LABELS } from "../lib/platforms";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { PlatformSyncPanel } from "../components/settings/PlatformSyncPanel";
import { PlatformCredentialsForm } from "../components/settings/PlatformCredentialsForm";
import { MarginFeeSettings } from "../components/settings/MarginFeeSettings";
import { ShippingProfileSettings } from "../components/settings/ShippingProfileSettings";
import { CurrencySettings } from "../components/settings/CurrencySettings";
import { Tabs, type TabDef } from "../components/common/Tabs";
import { useShopIconUrl } from "../hooks/useShopIconUrl";

export const Route = createFileRoute("/settings")({
  component: Settings,
});

// Only platforms with a real adapter implemented — Shopify is in the ListingPlatform
// enum for future use but has no adapter yet, so it isn't offered here.
const CONNECTABLE_PLATFORMS: ListingPlatform[] = ["etsy", "ebay"];

const SETTINGS_TABS: TabDef[] = [
  { id: "connection", label: "Connection" },
  { id: "integrations", label: "Integrations" },
  { id: "pricing", label: "Pricing" },
  { id: "general", label: "General" },
  { id: "reference", label: "Reference data" },
];

function Settings() {
  const [activeTab, setActiveTab] = useState("connection");
  const [backendUrl, setBackendUrl] = useState("");
  const [sharedPassword, setSharedPassword] = useState("");
  const [savedBackendUrl, setSavedBackendUrl] = useState("");
  const [savedSharedPassword, setSavedSharedPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [testResult, setTestResult] = useState<"idle" | "ok" | "fail" | "testing">("idle");
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [showConnectionFields, setShowConnectionFields] = useState(false);

  useEffect(() => {
    getSettings().then((s) => {
      setBackendUrl(s.backendUrl ?? "");
      setSharedPassword(s.sharedPassword ?? "");
      setSavedBackendUrl(s.backendUrl ?? "");
      setSavedSharedPassword(s.sharedPassword ?? "");
      setSettingsLoaded(true);
      // Auto-provisioned connections (the common case for the packaged app) start
      // collapsed — nothing for the user to do here. Show the fields up front only when
      // there's actually something missing to fill in.
      setShowConnectionFields(!s.backendUrl || !s.sharedPassword);
    });
  }, []);

  const isDirty = backendUrl !== savedBackendUrl || sharedPassword !== savedSharedPassword;

  const handleSave = async () => {
    await saveSettings({ backendUrl, sharedPassword });
    setSavedBackendUrl(backendUrl);
    setSavedSharedPassword(sharedPassword);
  };

  const handleTest = async () => {
    setTestResult("testing");
    const ok = await healthCheck(backendUrl);
    setTestResult(ok ? "ok" : "fail");
  };

  return (
    <div className="max-w-5xl flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Settings</h1>

      <Tabs tabs={SETTINGS_TABS} active={activeTab} onChange={setActiveTab} />

      {activeTab === "connection" && (
        <div className="max-w-md flex flex-col gap-4">
          {settingsLoaded && backendUrl && sharedPassword && (
            <p className="text-sm text-slate-500">
              Connected to <span className="font-medium text-slate-700">{backendUrl}</span>.
            </p>
          )}

          <button
            type="button"
            onClick={() => setShowConnectionFields((v) => !v)}
            className="self-start text-sm text-slate-600 underline"
          >
            {showConnectionFields ? "Hide" : "Show"} advanced connection settings
          </button>

          {showConnectionFields && (
            <>
              <label className="flex flex-col gap-1">
                <span className="text-sm font-medium">Backend URL</span>
                <input
                  className="rounded border border-slate-300 px-3 py-2 disabled:bg-slate-50 disabled:text-slate-400"
                  placeholder="http://homebase.tailnet-name.ts.net:8000"
                  value={backendUrl}
                  disabled={!settingsLoaded}
                  onChange={(e) => setBackendUrl(e.target.value)}
                />
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-sm font-medium">Shared password</span>
                <div className="flex gap-2">
                  <input
                    type={showPassword ? "text" : "password"}
                    className="flex-1 rounded border border-slate-300 px-3 py-2 disabled:bg-slate-50 disabled:text-slate-400"
                    value={sharedPassword}
                    disabled={!settingsLoaded}
                    onChange={(e) => setSharedPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="rounded border border-slate-300 px-3 py-1.5 text-sm"
                  >
                    {showPassword ? "Hide" : "Show"}
                  </button>
                </div>
              </label>

              <div className="flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={!isDirty}
                  className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Save
                </button>
                <button onClick={handleTest} className="rounded border border-slate-300 px-4 py-2">
                  Test connection
                </button>
              </div>

              {testResult === "testing" && <p className="text-slate-500">Testing…</p>}
              {testResult === "ok" && <p className="text-green-600">Connected successfully.</p>}
              {testResult === "fail" && <p className="text-red-600">Could not reach the backend.</p>}
            </>
          )}
        </div>
      )}

      {activeTab === "integrations" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          {CONNECTABLE_PLATFORMS.map((platform) => (
            <PlatformIntegrationCard key={platform} platform={platform} />
          ))}
        </div>
      )}

      {activeTab === "pricing" && (
        <div className="flex flex-col gap-2">
          <MarginFeeSettings />
          <ShippingProfileSettings />
        </div>
      )}

      {activeTab === "general" && (
        <div className="max-w-md flex flex-col gap-2">
          <CurrencySettings />
        </div>
      )}

      {activeTab === "reference" && (
        <div className="max-w-md flex flex-col gap-2">
          <Link to="/manufacturers" className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
            Manufacturers
          </Link>
          <Link to="/suppliers" className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
            Suppliers
          </Link>
          <Link to="/material-types" className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
            Material Types
          </Link>
        </div>
      )}
    </div>
  );
}

function PlatformIntegrationCard({ platform }: { platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  // Which environment to connect/edit credentials against — only meaningful for eBay
  // (the toggle only renders there); Etsy always uses "production". Local UI state, not
  // server state: it picks which environment's credentials this card is showing/editing
  // and which one "Connect" targets, independent of whatever's actually connected.
  const [environment, setEnvironment] = useState<PlatformEnvironment>("production");

  const { data: platformStatus } = useQuery({
    queryKey: ["platforms", platform, "status"],
    queryFn: () => platformsApi.status(platform),
  });

  const iconUrl = useShopIconUrl(
    platform,
    platformStatus?.has_shop_icon ?? false,
    platformStatus?.connected_at ?? null
  );

  const connectMutation = useMutation({
    mutationFn: async () => {
      const { authorize_url } = await platformsApi.connect(platform, environment);
      await openExternalUrl(authorize_url);
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: () => platformsApi.disconnect(platform),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platforms", platform, "status"] }),
  });

  const refreshStatus = () => queryClient.invalidateQueries({ queryKey: ["platforms", platform, "status"] });

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded border border-slate-300 p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {platformStatus?.connected && iconUrl && (
              <img src={iconUrl} alt="" className="h-8 w-8 rounded-full object-cover" />
            )}
            <div>
              <p className="font-medium">{label}</p>
              {platformStatus?.connected ? (
                <p className="text-sm text-green-700">
                  {platformStatus.shop_name ?? `Connected — account ${platformStatus.account_id}`}
                  {platformStatus.environment === "sandbox" && (
                    <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
                      Sandbox
                    </span>
                  )}
                </p>
              ) : (
                <p className="text-sm text-slate-500">Not connected</p>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={refreshStatus} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
              Refresh
            </button>
            {platformStatus?.connected ? (
              <button
                onClick={() => disconnectMutation.mutate()}
                className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-600"
              >
                Disconnect
              </button>
            ) : (
              <button
                onClick={() => connectMutation.mutate()}
                className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
              >
                Connect
              </button>
            )}
          </div>
        </div>
        <ErrorBanner error={connectMutation.error ?? disconnectMutation.error} />
        <PlatformCredentialsForm platform={platform} environment={environment} onEnvironmentChange={setEnvironment} />
      </div>
      {platformStatus?.connected && <PlatformSyncPanel platform={platform} />}
    </div>
  );
}
