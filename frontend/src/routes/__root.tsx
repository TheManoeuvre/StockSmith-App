import { createRootRoute, Link, Outlet, useMatchRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { SyncStatusIndicator } from "../components/common/SyncStatusIndicator";
import { MaintenanceOverlay } from "../components/common/MaintenanceOverlay";
import { UnsavedChangesDialog } from "../components/common/UnsavedChangesDialog";
import { DirtyRegistryProvider } from "../hooks/useDirtyRegistry";
import { GuardProvider, useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { dashboardApi } from "../api/dashboard";
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
  badge?: number;
  tone: NavBadgeTone;
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

  const blockedOrders =
    (summary?.orders_awaiting_inventory?.length ?? 0) + (summary?.orders_awaiting_packaging?.length ?? 0);
  const riskMaterials = summary?.low_stock_materials?.length ?? 0;
  const dueForCount = summary?.items_due_for_count_total ?? 0;
  const outstandingPurchases = purchases?.filter((p) => p.received_at === null).length ?? 0;

  return {
    dashboard: { badge: blockedOrders + riskMaterials, tone: (blockedOrders > 0 ? "hot" : "warm") as NavBadgeTone },
    materials: { badge: riskMaterials, tone: "warm" as NavBadgeTone },
    products: { badge: summary?.active_product_count ?? 0, tone: "neutral" as NavBadgeTone },
    purchases: { badge: outstandingPurchases, tone: (outstandingPurchases > 0 ? "warm" : "neutral") as NavBadgeTone },
    orders: { badge: blockedOrders, tone: "hot" as NavBadgeTone },
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
  const navItems: NavItem[] = [
    { label: "Dashboard", to: "/", ...badges.dashboard },
    { label: "Materials", to: "/materials", ...badges.materials },
    { label: "Products", to: "/products", ...badges.products },
    { label: "Purchases", to: "/purchases", ...badges.purchases },
    { label: "Orders", to: "/orders", ...badges.orders },
    { label: "Stock Take", to: "/stock-takes", badge: badges.stockTake.badge, tone: badges.stockTake.tone },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
      <aside className="flex w-[198px] flex-none flex-col border-r border-slate-200 bg-white p-2.5 pt-3.5">
        <div className="flex items-center gap-2 px-2 pb-4">
          <img src={appIcon} alt="StockSmith" className="block h-[26px] w-[26px] flex-none" />
          <div className="text-[13.5px] font-semibold tracking-tight">StockSmith</div>
        </div>
        <nav className="flex flex-col gap-px">
          {navItems.map((item) => (
            <NavButton key={item.to} item={item} />
          ))}
        </nav>
        <div className="flex-1" />
        <div className="flex flex-col gap-px border-t border-slate-200 pt-1.5">
          <SyncStatusIndicator />
          <NavButton item={{ label: "Settings", to: "/settings", tone: "neutral" }} />
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
