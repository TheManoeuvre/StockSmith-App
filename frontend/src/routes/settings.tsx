import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getSettings, openExternalUrl, saveSettings } from "../lib/tauri";
import { healthCheck } from "../api/client";
import { platformsApi } from "../api/platforms";
import type { ListingPlatform } from "../api/types";
import { PLATFORM_LABELS } from "../lib/platforms";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { PlatformSyncPanel } from "../components/settings/PlatformSyncPanel";
import { MarginFeeSettings } from "../components/settings/MarginFeeSettings";

export const Route = createFileRoute("/settings")({
  component: Settings,
});

// Only platforms with a real adapter implemented — Shopify is in the ListingPlatform
// enum for future use but has no adapter yet, so it isn't offered here.
const CONNECTABLE_PLATFORMS: ListingPlatform[] = ["etsy", "ebay"];

function Settings() {
  const [backendUrl, setBackendUrl] = useState("");
  const [sharedPassword, setSharedPassword] = useState("");
  const [savedBackendUrl, setSavedBackendUrl] = useState("");
  const [savedSharedPassword, setSavedSharedPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [testResult, setTestResult] = useState<"idle" | "ok" | "fail" | "testing">("idle");

  useEffect(() => {
    getSettings().then((s) => {
      setBackendUrl(s.backendUrl ?? "");
      setSharedPassword(s.sharedPassword ?? "");
      setSavedBackendUrl(s.backendUrl ?? "");
      setSavedSharedPassword(s.sharedPassword ?? "");
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
    <div className="max-w-md flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Settings</h1>

      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Backend URL</span>
        <input
          className="rounded border border-slate-300 px-3 py-2"
          placeholder="http://homebase.tailnet-name.ts.net:8000"
          value={backendUrl}
          onChange={(e) => setBackendUrl(e.target.value)}
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Shared password</span>
        <div className="flex gap-2">
          <input
            type={showPassword ? "text" : "password"}
            className="flex-1 rounded border border-slate-300 px-3 py-2"
            value={sharedPassword}
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

      <div className="mt-4 flex flex-col gap-3 border-t border-slate-200 pt-4">
        <h2 className="text-sm font-medium">Integrations</h2>
        {CONNECTABLE_PLATFORMS.map((platform) => (
          <PlatformIntegrationCard key={platform} platform={platform} />
        ))}
      </div>

      <div className="mt-4 flex flex-col gap-2 border-t border-slate-200 pt-4">
        <h2 className="text-sm font-medium">Pricing</h2>
        <MarginFeeSettings />
      </div>

      <div className="mt-4 flex flex-col gap-2 border-t border-slate-200 pt-4">
        <h2 className="text-sm font-medium">Reference data</h2>
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
    </div>
  );
}

function PlatformIntegrationCard({ platform }: { platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();

  const { data: platformStatus } = useQuery({
    queryKey: ["platforms", platform, "status"],
    queryFn: () => platformsApi.status(platform),
  });

  const connectMutation = useMutation({
    mutationFn: async () => {
      const { authorize_url } = await platformsApi.connect(platform);
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
          <div>
            <p className="font-medium">{label}</p>
            {platformStatus?.connected ? (
              <p className="text-sm text-green-700">
                Connected{platformStatus.account_id ? ` — account ${platformStatus.account_id}` : ""}
              </p>
            ) : (
              <p className="text-sm text-slate-500">Not connected</p>
            )}
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
      </div>
      {platformStatus?.connected && <PlatformSyncPanel platform={platform} />}
    </div>
  );
}
