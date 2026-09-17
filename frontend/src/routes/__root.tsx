import type { ReactNode } from "react";
import { createRootRoute, Link, Outlet, useMatchRoute } from "@tanstack/react-router";
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
import { SyncStatusIndicator } from "../components/common/SyncStatusIndicator";
import { NotificationCenter } from "../components/common/NotificationCenter";
import { MaintenanceOverlay } from "../components/common/MaintenanceOverlay";
import { UnsavedChangesDialog } from "../components/common/UnsavedChangesDialog";
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
  tone: NavBadgeTone;
}

/** A labelled run of nav items. Mirrors the Selling / Stock split the Settings page already uses. */
interface NavGroup {
  label: string;
  items: NavItem[];
}

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
    <Link
      to={item.to}
      className={`flex w-full items-center gap-2 rounded-md px-[9px] py-[7px] text-left text-[13px] font-medium hover:bg-slate-100 ${
        isActive ? "bg-slate-100 text-slate-900" : "text-slate-600"
      }`}
    >
      {item.icon}
      <span className="flex-1">{item.label}</span>
      {!!item.badge && (
        <span
          className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums ${BADGE_TONE_CLASSES[item.tone]}`}
        >
          {item.badge}
        </span>
      )}
    </Link>
  );
}

function RootChrome() {
  const badges = useNavBadges();
  const dashboard: NavItem = { label: "Dashboard", to: "/", icon: <DashboardIcon />, tone: "neutral" };
  // Ordered by the daily loop rather than by build order: orders come in, products fulfil
  // them, materials get consumed, purchases replenish, a stock take checks the books.
  const navGroups: NavGroup[] = [
    {
      label: "Sell",
      items: [
        { label: "Orders", to: "/orders", icon: <OrdersIcon />, ...badges.orders },
        { label: "Products", to: "/products", icon: <ProductsIcon />, tone: "neutral" },
      ],
    },
    {
      label: "Stock",
      items: [
        { label: "Materials", to: "/materials", icon: <MaterialsIcon />, ...badges.materials },
        { label: "Purchases", to: "/purchases", icon: <PurchasesIcon />, ...badges.purchases },
        { label: "Stock Take", to: "/stock-takes", icon: <StockTakeIcon />, ...badges.stockTake },
      ],
    },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
      <aside className="flex w-[198px] flex-none flex-col border-r border-slate-200 bg-white p-2.5 pt-3.5">
        <div className="flex items-center gap-2 px-2 pb-4">
          <img src={appIcon} alt="StockSmith" className="block h-[26px] w-[26px] flex-none" />
          <div className="text-[13.5px] font-semibold tracking-tight">StockSmith</div>
        </div>
        <nav className="flex flex-col gap-px">
          <NavButton item={dashboard} />
          {navGroups.map((group) => (
            <div key={group.label} className="flex flex-col gap-px">
              <p className="px-2.5 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400">
                {group.label}
              </p>
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
          <NavButton item={{ label: "Settings", to: "/settings", icon: <SettingsIcon />, tone: "neutral" }} />
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
