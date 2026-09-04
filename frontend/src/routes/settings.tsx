import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getSettings, openExternalUrl } from "../lib/tauri";
import { platformsApi, type PlatformEnvironment } from "../api/platforms";
import type { ListingPlatform } from "../api/types";
import { CONNECTABLE_PLATFORMS, PLATFORM_LABELS } from "../lib/platforms";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { SegmentedControl } from "../components/common/SegmentedControl";
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
import { SettingsCard } from "../components/settings/SettingsCard";
import { ListsMasterDetail } from "../components/settings/ListsMasterDetail";
import { BackgroundSyncSettings } from "../components/settings/BackgroundSyncSettings";
import { CurrencySettings } from "../components/settings/CurrencySettings";
import { ForecastSettings } from "../components/settings/ForecastSettings";
import { StockCountSettings } from "../components/settings/StockCountSettings";
import { DefaultKittingBomSettings } from "../components/settings/DefaultKittingBomSettings";
import { BackupSettings } from "../components/settings/BackupSettings";
import { ConnectionSettings } from "../components/settings/ConnectionSettings";
import { FieldMappingTable } from "../components/settings/FieldMappingTable";
import { SettingsNav, type SettingsNavGroup } from "../components/settings/SettingsNav";
import { useShopIconUrl } from "../hooks/useShopIconUrl";
import { DirtyPath } from "../hooks/useDirtyRegistry";

const PAGE_IDS = [
  "stores-sync",
  "pricing-fees",
  "shipping-packaging",
  "forecasting",
  "stock-counts",
  "lists",
  "backup-restore",
  "connection",
] as const;
type PageId = (typeof PAGE_IDS)[number];

const NAV_GROUPS: SettingsNavGroup[] = [
  {
    label: "Selling",
    items: [
      { id: "stores-sync", label: "Stores & sync" },
      { id: "pricing-fees", label: "Pricing & fees" },
      { id: "shipping-packaging", label: "Shipping & packaging" },
    ],
  },
  {
    label: "Stock",
    items: [
      { id: "forecasting", label: "Forecasting" },
      { id: "stock-counts", label: "Stock counts" },
      { id: "lists", label: "Lists" },
    ],
  },
  {
    label: "App",
    items: [
      { id: "backup-restore", label: "Backup & restore" },
      { id: "connection", label: "Connection" },
    ],
  },
];

export const Route = createFileRoute("/settings")({
  component: Settings,
  // Same reasoning as the product page (see routes/products/$productId.tsx): keeping the
  // active page in the URL makes switching pages a real router navigation, so the root
  // unsaved-changes blocker covers it without this route knowing the guard exists. It also
  // makes a section linkable, which Lists needs now that it holds the reference tables
  // themselves rather than links out to standalone pages.
  validateSearch: (search: Record<string, unknown>): { page?: PageId } => {
    const page = search.page;
    return PAGE_IDS.includes(page as PageId) ? { page: page as PageId } : {};
  },
});

function Settings() {
  const navigate = Route.useNavigate();
  const pageFromUrl = Route.useSearch().page;
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [hasConnection, setHasConnection] = useState(true);

  useEffect(() => {
    getSettings().then((s) => {
      setHasConnection(Boolean(s.backendUrl && s.sharedPassword));
      setSettingsLoaded(true);
    });
  }, []);

  // Stores & sync leads for everyone with a working connection — which, thanks to
  // auto-provisioning, is the overwhelmingly common case. Connection only jumps the queue when
  // there's genuinely something to fill in. The page's own editable copy of the store lives in
  // ConnectionSettings.
  const needsConnectionSetup = settingsLoaded && !hasConnection;
  // Re-checked here rather than trusting validateSearch to have dropped an unknown value: a page
  // id that matches nothing renders a blank content pane, which is a far worse failure than
  // ignoring a bad URL. The default can't live in validateSearch anyway — it depends on the
  // Tauri store, which the route loader has no access to.
  const activePage: PageId = PAGE_IDS.includes(pageFromUrl as PageId)
    ? (pageFromUrl as PageId)
    : needsConnectionSetup
      ? "connection"
      : "stores-sync";
  const setActivePage = (page: string) => navigate({ search: { page: page as PageId } });

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold tracking-[-.35px]">Settings</h1>

      <div className="flex items-start gap-6">
        <SettingsNav groups={NAV_GROUPS} active={activePage} onChange={setActivePage} />

        <div className="min-w-0 max-w-[840px] flex-1">
          {activePage === "stores-sync" && <StoresSyncPage />}
          {activePage === "pricing-fees" && <PricingFeesPage />}
          {activePage === "shipping-packaging" && <ShippingPackagingPage />}
          {activePage === "forecasting" && <ForecastSettings />}
          {activePage === "stock-counts" && <StockCountSettings />}
          {activePage === "lists" && <ListsMasterDetail />}
          {activePage === "backup-restore" && <BackupSettings />}
          {activePage === "connection" && <ConnectionSettings />}
        </div>
      </div>
    </div>
  );
}

function PricingFeesPage() {
  return (
    <div className="flex flex-col gap-4">
      <MarginFeeSettings />
      <SettingsCard title="Fee components" help="Per platform, used in the margin-after-fees figure.">
        {CONNECTABLE_PLATFORMS.map((platform) => (
          <PlatformFeeComponents key={platform} platform={platform} />
        ))}
      </SettingsCard>
      <CurrencySettings />
    </div>
  );
}

function ShippingPackagingPage() {
  return (
    <div className="flex flex-col gap-4">
      <ShippingProfileSettings />
      <DefaultKittingBomSettings />
    </div>
  );
}

function StoresSyncPage() {
  const [platformFilter, setPlatformFilter] = useState<ListingPlatform | "all">("all");
  const platforms = platformFilter === "all" ? CONNECTABLE_PLATFORMS : [platformFilter];

  return (
    <div className="flex flex-col gap-4">
      <SegmentedControl
        ariaLabel="Platform"
        value={platformFilter}
        onChange={setPlatformFilter}
        options={[
          { value: "all" as const, label: "All stores" },
          ...CONNECTABLE_PLATFORMS.map((p) => ({ value: p, label: PLATFORM_LABELS[p] })),
        ]}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {platforms.map((platform) => (
          <PlatformIntegrationCard key={platform} platform={platform} />
        ))}
      </div>

      {platformFilter === "all" && (
        <>
          <BackgroundSyncSettings />
          <FieldMappingTable />
        </>
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
    // Nested so each platform's editors sit under stores-sync/<platform>/…, which is what lets
    // a prefix veto target one card without catching the other. The trailing-slash convention in
    // isDirtyUnder keeps "ebay/" from matching a hypothetical "ebay-sandbox/".
    <DirtyPath segment="stores-sync">
      <DirtyPath segment={platform}>
        <div className="flex flex-col gap-2">
          <div className="rounded-[9px] border border-slate-200 bg-white p-3" style={{ boxShadow: "0 1px 2px rgba(15,23,42,.04)" }}>
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
            <p className="mt-2 text-xs text-slate-400">
              Per-profile {label} shipping costs live under Shipping & packaging.
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
