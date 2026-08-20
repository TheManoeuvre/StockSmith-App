import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getSettings, openExternalUrl, saveSettings } from "../lib/tauri";
import { healthCheck } from "../api/client";
import { platformsApi, type PlatformEnvironment } from "../api/platforms";
import type { ListingPlatform } from "../api/types";
import { CONNECTABLE_PLATFORMS, PLATFORM_LABELS } from "../lib/platforms";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { PlatformSyncPanel } from "../components/settings/PlatformSyncPanel";
import { PlatformCompatibilityPanel } from "../components/settings/PlatformCompatibilityPanel";
import { EtsyBackfillPanel } from "../components/settings/EtsyBackfillPanel";
import { PlatformLimitsEditor } from "../components/settings/PlatformLimitsEditor";
import { ListingProfiles } from "../components/settings/ListingProfiles";
import { EtsyProfileProposalsPanel } from "../components/settings/EtsyProfileProposalsPanel";
import { PlatformCredentialsForm } from "../components/settings/PlatformCredentialsForm";
import { EbaySigningKeyPanel } from "../components/settings/EbaySigningKeyPanel";
import { MarginFeeSettings } from "../components/settings/MarginFeeSettings";
import { PlatformFeeComponents } from "../components/settings/PlatformFeeComponents";
import { ShippingProfileSettings } from "../components/settings/ShippingProfileSettings";
import { ReferenceDataTable } from "../components/reference/ReferenceDataTable";
import { manufacturersApi } from "../api/manufacturers";
import { suppliersApi } from "../api/suppliers";
import { materialCategoriesApi } from "../api/materialCategories";
import { materialTypesApi } from "../api/materialTypes";
import { productCategoriesApi } from "../api/productCategories";
import { coloursApi } from "../api/colours";
import { BackgroundSyncSettings } from "../components/settings/BackgroundSyncSettings";
import { CurrencySettings } from "../components/settings/CurrencySettings";
import { ForecastSettings } from "../components/settings/ForecastSettings";
import { StockCountSettings } from "../components/settings/StockCountSettings";
import { DefaultKittingBomSettings } from "../components/settings/DefaultKittingBomSettings";
import { BackupSettings } from "../components/settings/BackupSettings";
import { Tabs, type TabDef } from "../components/common/Tabs";
import { useShopIconUrl } from "../hooks/useShopIconUrl";
import { DirtyPath, useDirtyRegistration } from "../hooks/useDirtyRegistry";

const TAB_IDS = ["general", "integrations", "pricing", "reference", "backup", "connection"] as const;
type TabId = (typeof TAB_IDS)[number];

export const Route = createFileRoute("/settings")({
  component: Settings,
  // Same reasoning as the product page (see routes/products/$productId.tsx): keeping the
  // active tab in the URL makes switching tabs a real router navigation, so the root
  // unsaved-changes blocker covers it without this route knowing the guard exists. It also
  // makes a section linkable, which the reference-data tab needs now that it holds the tables
  // themselves rather than links out to standalone pages.
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    const tab = search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

const SETTINGS_TABS: TabDef[] = [
  { id: "general", label: "General" },
  { id: "integrations", label: "Integrations" },
  { id: "pricing", label: "Pricing" },
  { id: "reference", label: "Reference data" },
  { id: "backup", label: "Backup" },
  { id: "connection", label: "Connection" },
];

function Settings() {
  const navigate = Route.useNavigate();
  const tabFromUrl = Route.useSearch().tab;
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

  // General leads for everyone with a working connection — which, thanks to auto-provisioning,
  // is the overwhelmingly common case. Connection only jumps the queue when there's genuinely
  // something to fill in, reusing the same condition that decides whether to expand its fields.
  const needsConnectionSetup = settingsLoaded && (!backendUrl || !sharedPassword);
  // Re-checked here rather than trusting validateSearch to have dropped an unknown value: a tab
  // id that matches nothing renders a page with a heading and no content, which is a far worse
  // failure than ignoring a bad URL. The default can't live in validateSearch anyway — it
  // depends on the Tauri store, which the route loader has no access to.
  const activeTab: TabId = TAB_IDS.includes(tabFromUrl as TabId)
    ? (tabFromUrl as TabId)
    : needsConnectionSetup
      ? "connection"
      : "general";
  const setActiveTab = (tab: string) => navigate({ search: { tab: tab as TabId } });

  const isDirty = backendUrl !== savedBackendUrl || sharedPassword !== savedSharedPassword;
  // This block already knew whether it was dirty; it just never told anyone. Registering it is
  // what makes navigating away — or switching tabs, now that's a navigation — prompt instead of
  // silently dropping a half-typed backend URL. Everything else (blocker, dialog) is already
  // mounted at the root.
  useDirtyRegistration("connection", "Connection settings", isDirty);

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
        <div className="max-w-2xl flex flex-col gap-4">
          <MarginFeeSettings />
        </div>
      )}

      {activeTab === "general" && (
        <div className="max-w-2xl flex flex-col gap-4">
          <BackgroundSyncSettings />
          <CurrencySettings />
          <ForecastSettings />
          <StockCountSettings />
          <DefaultKittingBomSettings />
        </div>
      )}

      {activeTab === "reference" && (
        <div className="max-w-3xl flex flex-col gap-4">
          <ReferenceDataTable
            title="Manufacturers"
            description="Who makes a material. Renaming one updates every material that uses it."
            segment="manufacturers"
            queryKey={["manufacturers"]}
            api={{
              list: manufacturersApi.list,
              create: manufacturersApi.findOrCreate,
              update: manufacturersApi.update,
              remove: manufacturersApi.remove,
              merge: manufacturersApi.merge,
            }}
            fields={[
              { key: "name", label: "Name" },
              { key: "website_url", label: "Website", type: "url", placeholder: "https://…" },
            ]}
            usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
          />
          <ReferenceDataTable
            title="Suppliers"
            description="Who you buy from. Renaming one updates every material and purchase that uses it."
            segment="suppliers"
            queryKey={["suppliers"]}
            api={{
              list: suppliersApi.list,
              create: suppliersApi.findOrCreate,
              update: suppliersApi.update,
              remove: suppliersApi.remove,
              merge: suppliersApi.merge,
            }}
            fields={[
              { key: "name", label: "Name" },
              { key: "website_url", label: "Website", type: "url", placeholder: "https://…" },
            ]}
            usageLabel={(n) => `${n} record${n === 1 ? "" : "s"}`}
          />
          <ReferenceDataTable
            title="Material types"
            description="What a material is made of. Renaming one updates every material that uses it."
            segment="material-types"
            queryKey={["material-types"]}
            api={{
              list: materialTypesApi.list,
              create: materialTypesApi.findOrCreate,
              update: materialTypesApi.update,
              remove: materialTypesApi.remove,
              merge: materialTypesApi.merge,
            }}
            fields={[{ key: "name", label: "Name" }]}
            usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
          />
          <ReferenceDataTable
            title="Material categories"
            description="What kind of thing a material is. The checkboxes are the behaviour that used to be hardcoded to filament and packaging — set them on any category, including ones you add. Order decides how the materials list groups and sorts."
            segment="material-categories"
            queryKey={["material-categories"]}
            api={{
              list: materialCategoriesApi.list,
              create: materialCategoriesApi.findOrCreate,
              update: materialCategoriesApi.update,
              remove: materialCategoriesApi.remove,
              merge: materialCategoriesApi.merge,
              reorder: materialCategoriesApi.reorder,
            }}
            fields={[
              { key: "name", label: "Name" },
              {
                key: "default_unit",
                label: "Default unit",
                type: "select",
                placeholder: "Leave unchanged",
                options: [
                  { value: "g", label: "g" },
                  { value: "ml", label: "ml" },
                  { value: "each", label: "each" },
                ],
              },
              { key: "tracks_colour", label: "Has a colour", type: "checkbox" },
              { key: "tracks_material_type", label: "Has a material type", type: "checkbox" },
              { key: "cost_per_kg_display", label: "Show cost per kg", type: "checkbox" },
              { key: "consumed_on_failed_build", label: "Consumed by failed builds", type: "checkbox" },
              { key: "auto_kitting_per_order", label: "Kitting: one per order, not per unit", type: "checkbox" },
            ]}
            usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
          />
          <ReferenceDataTable
            title="Product categories"
            description="What kind of thing a product is. Groups products for stock-count scheduling and for scoping a stock take. Renaming one updates every product that uses it."
            segment="product-categories"
            queryKey={["product-categories"]}
            api={{
              list: productCategoriesApi.list,
              create: productCategoriesApi.findOrCreate,
              update: productCategoriesApi.update,
              remove: productCategoriesApi.remove,
              merge: productCategoriesApi.merge,
            }}
            fields={[{ key: "name", label: "Name" }]}
            usageLabel={(n) => `${n} product${n === 1 ? "" : "s"}`}
          />
          <ReferenceDataTable
            title="Colours"
            description="Promoted from free text, so the same colour on several materials is one entry you can rename or merge. Duplicates like 'Black' and 'black' were folded together when this table was created."
            segment="colours"
            queryKey={["colours"]}
            api={{
              list: coloursApi.list,
              create: coloursApi.findOrCreate,
              update: coloursApi.update,
              remove: coloursApi.remove,
              merge: coloursApi.merge,
            }}
            fields={[
              { key: "name", label: "Name" },
              { key: "hex_code", label: "Hex code", placeholder: "#ff00aa" },
            ]}
            usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
          />
          {/* Shipping profiles are reference data too — a named row that products, variants and
              orders point at. They sat under Pricing only because their eBay/Etsy cost columns
              looked like a pricing concern; those are per-(platform × profile), so they belong
              on the profile itself. */}
          <ShippingProfileSettings />
        </div>
      )}

      {activeTab === "backup" && (
        <div className="max-w-3xl">
          <BackupSettings />
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
    // Nested so each platform's editors sit under integrations/<platform>/…, which is what lets
    // a prefix veto target one card without catching the other. The trailing-slash convention in
    // isDirtyUnder keeps "ebay/" from matching a hypothetical "ebay-sandbox/".
    <DirtyPath segment="integrations">
      <DirtyPath segment={platform}>
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
        {platform === "ebay" && <EbaySigningKeyPanel environment={environment} />}
        {/* Keyed by platform alone, so it belongs to the platform. The global "which channel am
            I estimating for" switch stays in Pricing — see MarginFeeSettings. */}
        <PlatformFeeComponents platform={platform} />
        <p className="mt-2 text-xs text-slate-400">
          Per-profile {label} shipping costs live under Reference data → Shipping profiles.
        </p>
      </div>
      {platformStatus?.connected && <PlatformCompatibilityPanel platform={platform} />}
      {platformStatus?.connected && platform === "etsy" && <EtsyBackfillPanel />}
      {platformStatus?.connected && <ListingProfiles platform={platform} />}
      {platformStatus?.connected && platform === "etsy" && <EtsyProfileProposalsPanel />}
      {platformStatus?.connected && <PlatformLimitsEditor platform={platform} />}
      {platformStatus?.connected && <PlatformSyncPanel platform={platform} />}
    </div>
      </DirtyPath>
    </DirtyPath>
  );
}
