import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getSettings } from "../lib/tauri";
import type { ListingPlatform } from "../api/types";
import { CONNECTABLE_PLATFORMS, PLATFORM_LABELS } from "../lib/platforms";
import { SegmentedControl } from "../components/common/SegmentedControl";
import { MarginFeeSettings } from "../components/settings/MarginFeeSettings";
import { PlatformFeeComponents } from "../components/settings/PlatformFeeComponents";
import { ShippingProfileSettings } from "../components/settings/ShippingProfileSettings";
import { SettingsCard } from "../components/settings/SettingsCard";
import { ListsMasterDetail } from "../components/settings/ListsMasterDetail";
import { CurrencySettings } from "../components/settings/CurrencySettings";
import { ForecastSettings } from "../components/settings/ForecastSettings";
import { StockCountSettings } from "../components/settings/StockCountSettings";
import { DefaultKittingBomSettings } from "../components/settings/DefaultKittingBomSettings";
import { BackupSettings } from "../components/settings/BackupSettings";
import { NotificationSettings } from "../components/settings/NotificationSettings";
import { ConnectionSettings } from "../components/settings/ConnectionSettings";
import { SettingsNav, type SettingsNavGroup } from "../components/settings/SettingsNav";
import { StoresHub } from "../components/settings/stores/StoresHub";
import { StorePage } from "../components/settings/stores/StorePage";

const PAGE_IDS = [
  "stores-sync",
  "pricing-fees",
  "shipping-packaging",
  "forecasting",
  "stock-counts",
  "lists",
  "backup-restore",
  "notifications",
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
      { id: "notifications", label: "Notifications" },
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
  // `store` narrows Stores & sync to one store's page. It lives in the URL for the same
  // reasons as `page`: switching stores is then a navigation the guard sees, and a store
  // page is linkable — the sidebar's sync indicator deep-links to the store with the problem.
  validateSearch: (search: Record<string, unknown>): { page?: PageId; store?: ListingPlatform } => {
    const page = search.page;
    const store = search.store;
    return {
      ...(PAGE_IDS.includes(page as PageId) ? { page: page as PageId } : {}),
      ...(CONNECTABLE_PLATFORMS.includes(store as ListingPlatform)
        ? { store: store as ListingPlatform }
        : {}),
    };
  },
});

function Settings() {
  const navigate = Route.useNavigate();
  const { page: pageFromUrl, store } = Route.useSearch();
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
          {activePage === "stores-sync" && (
            <StoresSyncPage
              store={store}
              onStoreChange={(next) => navigate({ search: { page: "stores-sync", store: next } })}
            />
          )}
          {activePage === "pricing-fees" && <PricingFeesPage />}
          {activePage === "shipping-packaging" && <ShippingPackagingPage />}
          {activePage === "forecasting" && <ForecastSettings />}
          {activePage === "stock-counts" && <StockCountSettings />}
          {activePage === "lists" && <ListsMasterDetail />}
          {activePage === "backup-restore" && <BackupSettings />}
          {activePage === "notifications" && <NotificationSettings />}
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

function StoresSyncPage({
  store,
  onStoreChange,
}: {
  store: ListingPlatform | undefined;
  onStoreChange: (store: ListingPlatform | undefined) => void;
}) {
  return (
    <div className="flex flex-col items-start gap-4">
      <SegmentedControl
        ariaLabel="Store"
        value={store ?? "all"}
        onChange={(next) => onStoreChange(next === "all" ? undefined : next)}
        options={[
          { value: "all" as const, label: "All stores" },
          ...CONNECTABLE_PLATFORMS.map((p) => ({
            value: p,
            label: PLATFORM_LABELS[p],
          })),
        ]}
      />
      <div className="w-full">
        {store ? <StorePage key={store} platform={store} /> : <StoresHub onOpenStore={onStoreChange} />}
      </div>
    </div>
  );
}
