import { useEffect, type ReactNode } from "react";
import { createRootRoute, Link, Outlet, useMatchRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  DashboardIcon,
  MaterialsIcon,
  OrdersIcon,
  ProductsIcon,
  PurchasesIcon,
  SettingsIcon,
  StockTakeIcon,
} from "../components/common/NavIcons";
import { NavTip } from "../components/common/NavTip";
import { SyncStatusIndicator } from "../components/common/SyncStatusIndicator";
import { NotificationCenter } from "../components/common/NotificationCenter";
import { MaintenanceOverlay } from "../components/common/MaintenanceOverlay";
import { UnsavedChangesDialog } from "../components/common/UnsavedChangesDialog";
import { useRefreshOnSync } from "../hooks/useRefreshOnSync";
import { useAppUpdate } from "../hooks/useAppUpdate";
import { onCheckForUpdatesRequested } from "../lib/updater";
import { DirtyRegistryProvider } from "../hooks/useDirtyRegistry";
import { GuardProvider, useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { dashboardApi } from "../api/dashboard";
import { ordersApi } from "../api/orders";
import { purchasesApi } from "../api/purchases";
import appIcon from "../assets/app-icon.png";

export const Route = createRootRoute({
  component: RootLayout,
});

/**
 * The unsaved-changes registry lives at the root, not on any one page.
 *
 * It has to be app-wide because the router blocker fires when you navigate from one page to
 * another, not just between tabs of the same one. Individual editors register themselves
 * wherever they happen to be mounted; pages only reach for useGuard() when they have their
 * own destructive controls to veto.
 */
function RootLayout() {
  return (
    <DirtyRegistryProvider>
      <RootShell />
    </DirtyRegistryProvider>
  );
}

function RootShell() {
  const guard = useUnsavedChangesGuard();
  const navigate = useNavigate();
  // The tray's "Check for updates". Settings > About doesn't need the backend, so this works
  // even while the Reconnecting overlay is up (the overlay stands down on /settings).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void onCheckForUpdatesRequested(() => navigate({ to: "/settings", search: { page: "about" } })).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [navigate]);
  // No window-close prompt any more, deliberately. Closing the window now hides it to the
  // tray (see src-tauri/lib.rs's on_window_event), so unsaved work isn't going anywhere —
  // the window is still there, with the form still in it. Asking "discard your changes?"
  // for something that discards nothing trains people to click through the dialog that
  // does matter. Quitting from the tray is the only path that can now lose work, and it
  // carries its own confirmation.
  return (
    <GuardProvider guard={guard}>
      <RootChrome />
      <UnsavedChangesDialog {...guard.dialogProps} />
      {/* App-wide for the same reason the guard is: a restore on the host locks out every page,
          not just Settings. */}
      <MaintenanceOverlay />
    </GuardProvider>
  );
}

type NavBadgeTone = "neutral" | "warm" | "hot";

interface NavItem {
  label: string;
  to: string;
  icon: ReactNode;
  badge?: number;
  /** A bare dot with no count, for "something here wants a look" that isn't a number. */
  dot?: boolean;
  tone: NavBadgeTone;
  /** What the page is for — shown in the tooltip when the sidebar is collapsed to icons. */
  hint: string;
}

/** A labelled run of nav items. Mirrors the Selling / Stock split the Settings page already uses. */
interface NavGroup {
  label: string;
  items: NavItem[];
}

const DOT_TONE_CLASSES: Record<NavBadgeTone, string> = {
  neutral: "bg-slate-400",
  warm: "bg-amber-500",
  hot: "bg-red-600",
};

const BADGE_TONE_CLASSES: Record<NavBadgeTone, string> = {
  neutral: "text-slate-600 bg-slate-100",
  warm: "text-amber-800 bg-amber-100",
  hot: "text-red-800 bg-red-100",
};

/**
 * Badge counts reuse the same query keys as the pages that own this data
 * (["dashboard-summary"] from the Dashboard route, ["purchases"] from the Purchases list) so
 * React Query serves this from the same cache entry rather than issuing a second request —
 * the sidebar is mounted on every page, so a dedicated fetch here would run constantly.
 *
 * A badge means "something here wants doing". Dashboard and Products deliberately have none:
 * the Dashboard figure was the sum of the Orders and Materials badges (the same problem
 * counted twice in one column), and the Products figure was a plain count of active products
 * that never changed tone and asked for nothing.
 */
function useNavBadges() {
  // Mounted here rather than on the Orders page: the sidebar is on every page, and a background
  // import must refresh the badge even if the user never opens Orders.
  useRefreshOnSync();
  const { data: summary } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: dashboardApi.summary,
  });
  const { data: purchases } = useQuery({
    queryKey: ["purchases"],
    queryFn: () => purchasesApi.list(),
  });

  // Orders: the number awaiting shipment — the same figure the Orders page shows on its
  // "Awaiting Shipment" tab, served from that page's ["order-counts", "awaiting"] cache entry.
  // Tone escalates to "hot" when any of them is blocked (no BOM to build the shortfall from).
  const { data: awaitingOrders } = useQuery({
    queryKey: ["order-counts", "awaiting"],
    queryFn: () => ordersApi.list(1, 0, "awaiting").then((p) => p.total),
  });
  const blockedOrders = summary?.orders_awaiting_inventory?.filter((o) => !o.has_bom).length ?? 0;
  const riskMaterials = summary?.low_stock_materials?.length ?? 0;
  const dueForCount = summary?.items_due_for_count_total ?? 0;
  const outstandingPurchases = purchases?.filter((p) => p.received_at === null).length ?? 0;

  return {
    materials: { badge: riskMaterials, tone: "warm" as NavBadgeTone },
    purchases: { badge: outstandingPurchases, tone: (outstandingPurchases > 0 ? "warm" : "neutral") as NavBadgeTone },
    orders: {
      badge: awaitingOrders ?? 0,
      tone: (blockedOrders > 0 ? "hot" : (awaitingOrders ?? 0) > 0 ? "warm" : "neutral") as NavBadgeTone,
    },
    stockTake: { badge: dueForCount, tone: (dueForCount > 0 ? "warm" : "neutral") as NavBadgeTone },
  };
}

function NavButton({ item }: { item: NavItem }) {
  const matchRoute = useMatchRoute();
  const isActive = !!matchRoute({ to: item.to, fuzzy: item.to !== "/" });
  return (
    <div className="group relative">
      <Link
        to={item.to}
        aria-label={item.label}
        className={`flex w-full items-center gap-2 rounded-md px-[9px] py-[7px] text-left text-[13px] font-medium hover:bg-slate-100 max-lg:justify-center ${
          isActive ? "bg-slate-100 text-slate-900" : "text-slate-600"
        }`}
      >
        {item.icon}
        <span className="flex-1 max-lg:hidden">{item.label}</span>
        {!!item.badge && (
          <span
            className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums max-lg:hidden ${BADGE_TONE_CLASSES[item.tone]}`}
          >
            {item.badge}
          </span>
        )}
        {/* Collapsed, the count gives way to a dot on the icon's corner: a nudge that something
            here wants doing. The number is in the tooltip. */}
        {!!item.badge && (
          <span
            aria-hidden="true"
            className={`absolute right-1.5 top-1 h-2 w-2 rounded-full ring-2 ring-white lg:hidden ${DOT_TONE_CLASSES[item.tone]}`}
          />
        )}
        {item.dot && (
          <span
            role="img"
            aria-label="Update available"
            className={`h-2 w-2 flex-none rounded-full max-lg:absolute max-lg:right-1.5 max-lg:top-1 max-lg:ring-2 max-lg:ring-white ${DOT_TONE_CLASSES[item.tone]}`}
          />
        )}
      </Link>
      <NavTip label={item.label} hint={item.hint} badge={item.badge} />
    </div>
  );
}

function RootChrome() {
  const badges = useNavBadges();
  // Mounted here, on every page, so the scheduled release check runs for as long as the app is
  // open rather than only while Settings is.
  const { available: updateAvailable } = useAppUpdate();
  const dashboard: NavItem = {
    label: "Dashboard",
    to: "/",
    icon: <DashboardIcon />,
    tone: "neutral",
    hint: "Overview of what needs doing today",
  };
  // Ordered by the daily loop rather than by build order: orders come in, products fulfil
  // them, materials get consumed, purchases replenish, a stock take checks the books.
  const navGroups: NavGroup[] = [
    {
      label: "Sell",
      items: [
        { label: "Orders", to: "/orders", icon: <OrdersIcon />, hint: "Orders from your stores, awaiting shipment", ...badges.orders },
        { label: "Products", to: "/products", icon: <ProductsIcon />, tone: "neutral", hint: "What you make, with recipes and listings" },
      ],
    },
    {
      label: "Stock",
      items: [
        { label: "Materials", to: "/materials", icon: <MaterialsIcon />, hint: "Raw materials and how much is left", ...badges.materials },
        { label: "Purchases", to: "/purchases", icon: <PurchasesIcon />, hint: "Supplier orders and deliveries", ...badges.purchases },
        { label: "Stock Take", to: "/stock-takes", icon: <StockTakeIcon />, hint: "Count stock and correct the books", ...badges.stockTake },
      ],
    },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
      {/* Collapses to an icon rail below the lg breakpoint to give the page more room; the labels
          come back as hover/focus tooltips (NavTip). */}
      <aside className="flex w-[52px] flex-none flex-col border-r border-slate-200 bg-white p-1.5 pt-3.5 lg:w-[198px] lg:p-2.5 lg:pt-3.5">
        <div className="flex items-center gap-2 px-2 pb-4 max-lg:justify-center max-lg:px-0">
          <img src={appIcon} alt="StockSmith" className="block h-[26px] w-[26px] flex-none" />
          <div className="text-[13.5px] font-semibold tracking-tight max-lg:hidden">StockSmith</div>
        </div>
        <nav className="flex flex-col gap-px">
          <NavButton item={dashboard} />
          {navGroups.map((group) => (
            <div key={group.label} className="flex flex-col gap-px">
              <p className="px-2.5 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400 max-lg:hidden">
                {group.label}
              </p>
              <div className="mx-2 mt-2 border-t border-slate-200 lg:hidden" aria-hidden="true" />
              {group.items.map((item) => (
                <NavButton key={item.to} item={item} />
              ))}
            </div>
          ))}
        </nav>
        <div className="flex-1" />
        <div className="flex flex-col gap-px border-t border-slate-200 pt-1.5">
          <NotificationCenter />
          <SyncStatusIndicator />
          <NavButton
            item={{
              label: "Settings",
              to: "/settings",
              icon: <SettingsIcon />,
              tone: "warm",
              dot: updateAvailable,
              hint: updateAvailable ? "A new version is available — see Settings › About" : "Stores, sync and app preferences",
            }}
          />
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
